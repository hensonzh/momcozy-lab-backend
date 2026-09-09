from __future__ import annotations

import asyncio

from typing import Any, cast
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ...core.settings import Settings
from ..audit.service import AuditService
from .models import CareEpisode, CareOrder
from .schemas import CareEpisodeRead, CareOrderRead, PurchaseRead


class StripeCheckoutService:
    def __init__(self, session: AsyncSession, settings: Settings, audit: AuditService) -> None:
        if not settings.stripe_checkout_enabled:
            raise ApiError(code="payments_disabled", message="Stripe Checkout is unavailable.", status=403)
        self.session = session
        self.settings = settings
        self.audit = audit
        self.client = stripe.StripeClient(settings.stripe_secret_key, max_network_retries=2)

    async def create(self, owner: UUID, order_id: UUID, request_id: str) -> tuple[PurchaseRead, str]:
        order = await self._order(owner, order_id, lock=True)
        if order.payment_mode != "stripe":
            raise ApiError(code="payment_mode_mismatch", message="This order is not a Stripe order.", status=409)
        if order.status == "paid":
            return await self.purchase(owner, order), self.settings.stripe_checkout_return_origin
        if order.stripe_session_id:
            session = await asyncio.to_thread(self.client.v1.checkout.sessions.retrieve, order.stripe_session_id)
            if session.status == "open" and session.url:
                return await self.purchase(owner, order), session.url
            raise ApiError(code="checkout_expired", message="This checkout expired. Create a new order.", status=409)
        package_name = order.package_id.replace("-", " ").title()
        params: dict[str, Any] = {
            "mode": "payment", "line_items": [{"price_data": {"currency": order.currency.lower(), "unit_amount": order.price_minor,
                "product_data": {"name": f"MomCozy {package_name} care"}}, "quantity": 1}],
            "client_reference_id": str(order.id), "metadata": {"care_order_id": str(order.id)},
            "success_url": f"{self.settings.stripe_checkout_return_origin}/stripe-checkout/success?order_id={order.id}",
            "cancel_url": f"{self.settings.stripe_checkout_return_origin}/stripe-checkout/cancel?order_id={order.id}",
        }
        created = await asyncio.to_thread(self.client.v1.checkout.sessions.create, cast(Any, params))
        order.stripe_session_id = created.id
        order.stripe_livemode = bool(created.livemode)
        order.status = "processing"
        order.version += 1
        await self.session.flush()
        await self.audit.record(actor_user_id=owner, action="care.order.stripe_checkout.created", resource_type="care_order",
            resource_id=str(order.id), request_id=request_id, details={"stripe_livemode": order.stripe_livemode})
        if not created.url:
            raise ApiError(code="checkout_unavailable", message="Stripe did not return a checkout URL.", status=502)
        return await self.purchase(owner, order), created.url

    async def reconcile(self, owner: UUID, order_id: UUID, request_id: str) -> PurchaseRead:
        order = await self._order(owner, order_id, lock=True)
        if order.payment_mode != "stripe" or not order.stripe_session_id or order.status == "paid":
            return await self.purchase(owner, order)
        session = await asyncio.to_thread(self.client.v1.checkout.sessions.retrieve, order.stripe_session_id)
        if session.payment_status == "paid":
            await self._mark_paid(order, request_id)
        elif session.status == "expired":
            order.status = "failed"
            order.version += 1
        return await self.purchase(owner, order)

    async def webhook(self, payload: bytes, signature: str, request_id: str) -> None:
        try:
            event = stripe.Webhook.construct_event(payload, signature, self.settings.stripe_webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError) as exc:
            raise ApiError(code="invalid_webhook", message="Invalid Stripe webhook.", status=400) from exc
        if event.type not in {"checkout.session.completed", "checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed", "checkout.session.expired"}:
            return
        session = event.data.object
        order_id = (session.metadata or {}).get("care_order_id")
        if not order_id:
            return
        try:
            order_uuid = UUID(order_id)
        except ValueError:
            return
        order = await self.session.scalar(select(CareOrder).where(CareOrder.id == order_uuid).with_for_update())
        if order is None or order.stripe_session_id != session.id or order.stripe_livemode != bool(session.livemode):
            return
        if event.type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"} and session.payment_status == "paid":
            await self._mark_paid(order, request_id)
        elif event.type in {"checkout.session.async_payment_failed", "checkout.session.expired"} and order.status != "paid":
            order.status = "failed"
            order.version += 1

    async def _mark_paid(self, order: CareOrder, request_id: str) -> None:
        if order.status == "paid":
            return
        order.status = "paid"
        order.version += 1
        existing = await self.session.scalar(select(CareEpisode).where(CareEpisode.order_id == order.id))
        if existing is None:
            self.session.add(CareEpisode(owner_user_id=order.owner_user_id, order_id=order.id, package_id=order.package_id,
                status="active", stage="preparation", total_sessions=order.total_sessions, remaining_sessions=order.total_sessions))
        await self.session.flush()
        await self.audit.record(actor_user_id=order.owner_user_id, action="care.order.stripe_checkout.paid", resource_type="care_order",
            resource_id=str(order.id), request_id=request_id, details={"stripe_livemode": order.stripe_livemode})

    async def _order(self, owner: UUID, order_id: UUID, *, lock: bool = False) -> CareOrder:
        query = select(CareOrder).where(CareOrder.owner_user_id == owner, CareOrder.id == order_id)
        if lock:
            query = query.with_for_update()
        order = await self.session.scalar(query)
        if order is None:
            raise ApiError(code="not_found", message="Order not found.", status=404)
        return order

    async def purchase(self, owner: UUID, order: CareOrder) -> PurchaseRead:
        episode = await self.session.scalar(select(CareEpisode).where(CareEpisode.owner_user_id == owner, CareEpisode.order_id == order.id))
        return PurchaseRead(order=CareOrderRead.model_validate(order), episode=None if episode is None else CareEpisodeRead.model_validate(episode))
