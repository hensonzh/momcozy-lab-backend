from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from ...core.errors import ApiError
from ..audit.service import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .catalog import CATALOG, PACKAGES
from .models import CareEligibility, CareEpisode, CareOrder
from .repository import CareRepository
from .schemas import (CareEpisodeRead, CareOrderRead, CareOverview, CareProviderRead, EligibilityWrite,
    PurchaseRead, SandboxPaymentWrite, ServiceCatalog, ServicePackageRead)


class CareService:
    def __init__(self, repository: CareRepository, audit: AuditService, idempotency: IdempotencyService, *, sandbox_enabled: bool, stripe_enabled: bool = False, stripe_livemode: bool = False) -> None:
        self.repository = repository
        self.audit = audit
        self.idempotency = idempotency
        self.sandbox_enabled = sandbox_enabled and not stripe_enabled
        self.stripe_enabled = stripe_enabled
        self.stripe_livemode = stripe_livemode
        self.test_providers = not stripe_livemode

    async def catalog(self) -> ServiceCatalog:
        providers = await self.repository.providers(sandbox=self.test_providers) if self.sandbox_enabled or self.stripe_enabled else []
        return ServiceCatalog(packages=CATALOG, providers=[CareProviderRead.model_validate(value) for value in providers],
            available_regions=sorted({region for provider in providers for region in provider.regions}),
            payment_mode="stripe" if self.stripe_enabled else "sandbox" if self.sandbox_enabled else "disabled")

    async def overview(self, owner: UUID) -> CareOverview:
        return CareOverview(orders=[CareOrderRead.model_validate(value) for value in await self.repository.orders(owner)],
            episodes=[CareEpisodeRead.model_validate(value) for value in await self.repository.episodes(owner)])

    async def check_eligibility(self, owner: UUID, body: EligibilityWrite, request_id: str) -> CareEligibility:
        self._package(body.package_id)
        catalog = await self.catalog()
        eligible = (self.sandbox_enabled or self.stripe_enabled) and body.region in catalog.available_regions
        result = CareEligibility(owner_user_id=owner, package_id=body.package_id, region=body.region, eligible=eligible,
            reason="" if eligible else "region_unavailable", expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
        await self.repository.add(result)
        await self.audit.record(actor_user_id=owner, action="care.eligibility_checked", resource_type="care_eligibility",
            resource_id=str(result.id), request_id=request_id, details={"eligible": eligible})
        return result

    async def create_order(self, owner: UUID, eligibility_id: UUID, key: str, request_id: str) -> PurchaseRead:
        if not (self.sandbox_enabled or self.stripe_enabled):
            raise ApiError(code="payments_disabled", message="Purchases are unavailable.", status=403)
        await self.repository.lock_owner(owner)
        decision = await self.idempotency.reserve(actor_user_id=owner, scope="care.order.create", key=key,
            request_hash=request_hash({"eligibility_id": str(eligibility_id)}), expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        if decision.status == "replay":
            return await self.purchase(owner, parse_idempotency_response_ref(decision.record.response_ref))
        eligibility = await self.repository.eligibility(owner, eligibility_id)
        if eligibility is None:
            raise ApiError(code="not_found", message="Eligibility check not found.", status=404)
        expiry = eligibility.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc) or not eligibility.eligible:
            raise ApiError(code="eligibility_required", message="Confirm current service eligibility before purchase.", status=409)
        # Availability is rechecked; a stale eligibility response cannot mint an order.
        if eligibility.region not in (await self.catalog()).available_regions:
            raise ApiError(code="region_unavailable", message="This region is currently unavailable.", status=409)
        package = self._package(eligibility.package_id)
        order = await self.repository.existing_order(owner, package.id, payment_mode="stripe" if self.stripe_enabled else "sandbox")
        if order is None:
            order = CareOrder(owner_user_id=owner, eligibility_id=eligibility.id, package_id=package.id,
                price_minor=package.price_minor, currency=package.currency, region=eligibility.region,
                duration_days=package.duration_days, total_sessions=package.sessions,
                payment_mode="stripe" if self.stripe_enabled else "sandbox",
                stripe_livemode=self.stripe_livemode if self.stripe_enabled else None, status="pending", version=1)
            await self.repository.add(order)
            await self._audit(owner, order, "created", request_id)
        await self.idempotency.mark_completed(record=decision.record, response_ref=str(order.id))
        return await self.purchase(owner, order.id)

    async def purchase(self, owner: UUID, order_id: UUID) -> PurchaseRead:
        order = await self._order(owner, order_id)
        episode = await self.repository.episode_for_order(owner, order_id)
        return PurchaseRead(order=CareOrderRead.model_validate(order), episode=None if episode is None else CareEpisodeRead.model_validate(episode))

    async def sandbox_payment(self, owner: UUID, order_id: UUID, body: SandboxPaymentWrite, request_id: str) -> PurchaseRead:
        self._require_sandbox()
        order = await self._order(owner, order_id, lock=True)
        if order.payment_mode != "sandbox":
            raise ApiError(code="forbidden", message="Only sandbox orders support simulated payment.", status=403)
        target = {"succeeded": "paid", "declined": "failed", "requires_action": "requires_action", "reconciling": "reconciling", "cancelled": "cancelled"}[body.outcome]
        if order.status == target and order.version == body.expected_version + 1:
            return await self.purchase(owner, order_id)
        if order.version != body.expected_version or order.status in {"paid", "cancelled"}:
            raise ApiError(code="version_conflict", message="This order changed. Refresh its payment state.", status=409)
        order.status = target
        order.version += 1
        order.updated_at = datetime.now(timezone.utc)
        if target == "paid":
            episode = CareEpisode(owner_user_id=owner, order_id=order.id, package_id=order.package_id,
                status="active", stage="preparation", total_sessions=order.total_sessions, remaining_sessions=order.total_sessions)
            await self.repository.add(episode)
            from .events import record_care_event
            await record_care_event(self.repository.session, episode_id=episode.id, kind="service_progress_changed",
                aggregate_id=episode.id, aggregate_version=episode.version, actor_user_id=owner,
                recipient_id=None, occurred_at=order.updated_at)
        await self.repository.flush()
        await self._audit(owner, order, f"sandbox_payment.{target}", request_id)
        return await self.purchase(owner, order_id)

    def _require_sandbox(self) -> None:
        if not self.sandbox_enabled:
            raise ApiError(code="sandbox_disabled", message="Sandbox payments are disabled in this environment.", status=403)

    def _package(self, package_id: str) -> ServicePackageRead:
        package = PACKAGES.get(package_id)
        if package is None:
            raise ApiError(code="not_found", message="Service package not found.", status=404)
        return package

    async def _order(self, owner: UUID, order_id: UUID, *, lock: bool = False) -> CareOrder:
        order = await self.repository.order(owner, order_id, lock=lock)
        if order is None:
            raise ApiError(code="not_found", message="Order not found.", status=404)
        return order

    async def _audit(self, owner: UUID, order: CareOrder, action: str, request_id: str) -> None:
        await self.audit.record(actor_user_id=owner, action=f"care.order.{action}", resource_type="care_order",
            resource_id=str(order.id), request_id=request_id, details={"version": order.version, "payment_mode": order.payment_mode})
