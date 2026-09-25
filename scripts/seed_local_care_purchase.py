"""Add one sandbox purchased service for the existing local UI test account."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.core.settings import Settings
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService, IdempotencyService
from app.modules.auth.repository import AuthAccountRepository
from app.modules.baby.profile_models import BabyProfile  # noqa: F401 -- registers the episode foreign key
from app.modules.care.catalog import PACKAGES
from app.modules.care.models import CareProvider
from app.modules.care.repository import CareRepository
from app.modules.care.schemas import EligibilityWrite, SandboxPaymentWrite
from app.modules.care.service import CareService
from app.modules.users.models import User


async def main() -> None:
    settings = Settings.from_env()
    if settings.app_env != "local" or settings.stripe_checkout_enabled:
        raise RuntimeError("This fixture requires APP_ENV=local and sandbox payments.")
    engine = create_db_engine(settings)
    package = PACKAGES["milk-supply-care"]
    request_id = f"local-care-seed-{uuid4()}"
    try:
        async with create_session_factory(engine).begin() as session:
            user = await AuthAccountRepository(session).get_user_by_email(email="dev@example.test")
            if user is None or user.status != "active":
                raise RuntimeError("The active local test account must already exist.")
            repository = CareRepository(session)
            await repository.lock_owner(user.id)
            episodes = await repository.episodes(user.id)
            episode = next((item for item in episodes if item.package_id == package.id
                            and item.status in {"active", "paused", "provisioning_pending"}), None)
            created = episode is None
            if created:
                provider_id = uuid5(NAMESPACE_URL, "momcozy.local/ui-fixture/ibclc-jamie-lee")
                provider = await session.get(CareProvider, provider_id)
                if provider is None:
                    if await session.get(User, provider_id) is None:
                        session.add(User(id=provider_id))
                        await session.flush()
                    provider = CareProvider(
                        user_id=provider_id, display_name="Jamie Lee (Test)",
                        timezone="Asia/Shanghai", regions=["CA"], languages=["en"],
                        bio="Fictional consultant for local UI testing only; not a real consultation service.", active=True, sandbox=True,
                    )
                    session.add(provider)
                    await session.flush()
                provider.display_name = "Jamie Lee (Test)"
                provider.languages = ["en"]
                provider.bio = "Fictional consultant for local UI testing only; not a real consultation service."
                if not provider.sandbox or not provider.active:
                    raise RuntimeError("The fixture provider must be active and sandbox-only.")
                audit_repository = AuditRepository(session)
                audit = AuditService(repository=audit_repository)
                service = CareService(repository, audit,
                                      IdempotencyService(repository=audit_repository), sandbox_enabled=True)
                eligibility = await service.check_eligibility(
                    user.id, EligibilityWrite(package_id=package.id, region="CA",
                                              acknowledges_non_emergency=True), request_id,
                )
                purchase = await service.create_order(user.id, eligibility.id, request_id, request_id)
                purchase = await service.sandbox_payment(
                    user.id, purchase.order.id,
                    SandboxPaymentWrite(expected_version=purchase.order.version, outcome="succeeded"),
                    request_id,
                )
                episode = await repository.episode_for_order(user.id, purchase.order.id)
                assert episode is not None
                episode.assigned_ibclc_id = provider_id
                episode.starts_at = datetime.now(timezone.utc)
                episode.ends_at = episode.starts_at + timedelta(days=package.duration_days)
                episode.version += 1
                await audit.record(
                    actor_user_id=user.id, actor_type="system", actor_service="local_ui_fixture",
                    action="care.local_fixture.assigned", resource_type="care_episode",
                    resource_id=str(episode.id), request_id=request_id,
                    details={"sandbox": True, "provider_id": str(provider_id), "version": episode.version},
                )
                await session.flush()
            assert episode is not None
            provider = await session.get(CareProvider, episode.assigned_ibclc_id) if episode.assigned_ibclc_id else None
            result = {
                "account": "dev@example.test", "created": created, "episode_id": str(episode.id),
                "order_id": str(episode.order_id), "package": package.name, "status": episode.status,
                "stage": episode.stage, "provider": provider.display_name if provider else None,
                "duration_days": package.duration_days, "remaining_sessions": episode.remaining_sessions,
                "starts_at": episode.starts_at.isoformat() if episode.starts_at else None,
                "ends_at": episode.ends_at.isoformat() if episode.ends_at else None,
            }
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
