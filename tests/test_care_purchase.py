
from app.modules.baby.profile_models import BabyProfile
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4
from app.modules.care.event_models import CareServiceEvent

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.dependencies import require_current_user
from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.infrastructure.db.base import Base
from app.modules.audit.models import IdempotencyKey
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import IdempotencyService
from app.modules.auth import CurrentUser
from app.modules.care.models import CareEligibility, CareEpisode, CareOrder, CareProvider
from app.modules.care.repository import CareRepository
from app.modules.care.router import get_care_service
from app.modules.care.schemas import EligibilityWrite, OrderWrite, SandboxPaymentWrite
from app.modules.care.service import CareService
from app.modules.users.models import User


def test_strict_purchase_contract_excludes_patient_and_card_overrides():
    with pytest.raises(ValidationError):
        EligibilityWrite(package_id="feeding-confidence", region="CA", acknowledges_non_emergency=False)
    with pytest.raises(ValidationError):
        OrderWrite.model_validate({"eligibility_id": str(uuid4()), "price_minor": 0, "owner_user_id": str(uuid4())})
    with pytest.raises(ValidationError):
        SandboxPaymentWrite.model_validate({"expected_version": 1, "outcome": "succeeded", "card_number": "secret"})


def test_purchase_persists_recoverable_state_and_creates_exactly_one_owned_episode():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[User.__table__, BabyProfile.__table__,
                IdempotencyKey.__table__, CareProvider.__table__, CareEligibility.__table__, CareOrder.__table__, CareEpisode.__table__, CareServiceEvent.__table__]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, other, provider = uuid4(), uuid4(), uuid4()
        audit = AsyncMock()
        def service(session):
            return CareService(CareRepository(session), audit, IdempotencyService(repository=AuditRepository(session)), sandbox_enabled=True)
        async with sessions.begin() as session:
            session.add_all([User(id=owner), User(id=other), User(id=provider)])
            await session.flush()
            session.add(CareProvider(user_id=provider, display_name="Test IBCLC", timezone="America/Los_Angeles", regions=["CA"], languages=["en"], active=True, sandbox=True))
        async with sessions.begin() as session:
            current = service(session)
            catalog = await current.catalog()
            assert [(item.id, item.price_minor, item.duration_days, item.sessions) for item in catalog.packages] == [
                ("feeding-confidence", 21900, 7, 2), ("better-breastfeeding", 23900, 14, 2),
                ("milk-supply-care", 29900, 14, 3), ("comfortable-feeding", 21900, 3, 2)]
            assert catalog.available_regions == ["CA"]
            denied = await current.check_eligibility(owner, EligibilityWrite(package_id="feeding-confidence", region="TX", acknowledges_non_emergency=True), "tx")
            assert not denied.eligible
            eligible = await current.check_eligibility(owner, EligibilityWrite(package_id="feeding-confidence", region="CA", acknowledges_non_emergency=True), "ca")
            eligibility_id = eligible.id
        async with sessions() as session:
            with pytest.raises(ApiError) as denial:
                await service(session).create_order(owner, denied.id, "denied", "denied")
            assert denial.value.code == "eligibility_required"
            await session.rollback()
        async with sessions.begin() as session:
            result = await service(session).create_order(owner, eligibility_id, "purchase-key", "create")
            order_id = result.order.id
            assert result.order.status == "pending" and result.episode is None
            assert result.order.price_minor == 21900 and result.order.payment_mode == "sandbox"
        async with sessions.begin() as session:
            replay = await service(session).create_order(owner, eligibility_id, "purchase-key", "retry-create")
            assert replay.order.id == order_id
            challenge = await service(session).sandbox_payment(owner, order_id, SandboxPaymentWrite(expected_version=1, outcome="requires_action"), "challenge")
            assert challenge.order.status == "requires_action" and challenge.episode is None
        async with sessions.begin() as session:
            reconciling = await service(session).sandbox_payment(owner, order_id, SandboxPaymentWrite(expected_version=2, outcome="reconciling"), "reconcile")
            assert reconciling.order.status == "reconciling" and reconciling.episode is None
        async with sessions.begin() as session:
            current = service(session)
            paid = await current.sandbox_payment(owner, order_id, SandboxPaymentWrite(expected_version=3, outcome="succeeded"), "pay")
            assert paid.order.status == "paid" and paid.order.version == 4
            assert paid.episode is not None and paid.episode.remaining_sessions == 2
            episode_id = paid.episode.id
            retry = await current.sandbox_payment(owner, order_id, SandboxPaymentWrite(expected_version=3, outcome="succeeded"), "retry-pay")
            assert retry.episode.id == episode_id
        async with sessions.begin() as session:
            current = service(session)
            own = await current.overview(owner)
            assert len(own.orders) == 1 and len(own.episodes) == 1
            assert own.episodes[0].id == episode_id and own.episodes[0].order_id == order_id
            foreign = await current.overview(other)
            assert foreign.orders == [] and foreign.episodes == []
            with pytest.raises(ApiError) as hidden:
                await current.purchase(other, order_id)
            assert hidden.value.status == 404
            disabled = CareService(CareRepository(session), audit, IdempotencyService(repository=AuditRepository(session)), sandbox_enabled=False)
            assert (await disabled.catalog()).payment_mode == "disabled"
            with pytest.raises(ApiError) as no_simulation:
                await disabled.sandbox_payment(owner, order_id, SandboxPaymentWrite(expected_version=4, outcome="succeeded"), "production")
            assert no_simulation.value.code == "sandbox_disabled"
        assert sum(call.kwargs["action"] == "care.order.sandbox_payment.paid" for call in audit.record.call_args_list) == 1
        await engine.dispose()
    asyncio.run(run())


def test_care_routes_authenticate_and_use_the_session_owner():
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)
    assert client.get("/v1/care/catalog").status_code == 401
    owner = uuid4()
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(user_id=owner, subject=str(owner), session_id="test", token_id="test", roles=frozenset({"user"}), permissions=frozenset())
    current = AsyncMock()
    current.overview.return_value = {"orders": [], "episodes": []}
    app.dependency_overrides[get_care_service] = lambda: current
    assert client.get("/v1/care/overview?owner_user_id=another").status_code == 200
    assert current.overview.call_args.args == (owner,)
    assert client.post("/v1/care/orders", json={"eligibility_id": str(uuid4())}).status_code == 422
    current.create_order.assert_not_called()
