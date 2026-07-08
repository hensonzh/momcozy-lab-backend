from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.invites.models import InviteCode
from production_backend.app.modules.invites.service import InviteCodeService, validate_invite_code_for_login


def test_invite_code_service_creates_generated_code_and_disables_it() -> None:
    repository = FakeInviteCodeRepository()
    service = InviteCodeService(repository=repository)
    expires_at = datetime(2026, 8, 1, tzinfo=timezone.utc)

    created = _run(
        service.create_invite_code(
            label="Alice 内测",
            assigned_to="alice@example.test",
            expires_at=expires_at,
            actor_service="internal-service",
        )
    )
    disabled = _run(service.disable_invite_code(code=created.code.lower()))

    assert created.code.startswith("MCZ-")
    assert created.label == "Alice 内测"
    assert created.assigned_to == "alice@example.test"
    assert created.expires_at == expires_at
    assert created.created_by_service == "internal-service"
    assert disabled.status == "disabled"
    assert disabled.disabled_at is not None


def test_invite_code_service_rejects_duplicate_requested_code() -> None:
    repository = FakeInviteCodeRepository(existing=InviteCode(id=uuid4(), code="MCZ-DUPE-0001", status="active"))
    service = InviteCodeService(repository=repository)

    with pytest.raises(ApiError) as conflict:
        _run(service.create_invite_code(code="mcz-dupe-0001"))

    assert conflict.value.code == "conflict"


def test_invite_code_service_defaults_expiration_to_one_year() -> None:
    repository = FakeInviteCodeRepository()
    service = InviteCodeService(repository=repository)
    before = datetime.now(timezone.utc)

    created = _run(service.create_invite_code(code="MCZ-YEAR-0001"))

    after = datetime.now(timezone.utc)
    assert created.expires_at is not None
    assert before + timedelta(days=365) <= created.expires_at <= after + timedelta(days=365)


def test_invite_code_service_lists_with_pagination_metadata() -> None:
    repository = FakeInviteCodeRepository()
    service = InviteCodeService(repository=repository)

    page = _run(service.list_invite_codes(limit=20, offset=40))

    assert page.items == []
    assert page.total == 123
    assert page.limit == 20
    assert page.offset == 40
    assert page.has_more is True
    assert repository.list_kwargs == {"limit": 20, "offset": 40}


def test_invite_code_service_rejects_invalid_pagination() -> None:
    service = InviteCodeService(repository=FakeInviteCodeRepository())

    with pytest.raises(ApiError, match="offset"):
        _run(service.list_invite_codes(limit=20, offset=-1))


def test_validate_invite_code_for_login_rejects_disabled_expired_and_other_device() -> None:
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)

    with pytest.raises(ApiError, match="disabled"):
        validate_invite_code_for_login(invite_code=InviteCode(code="A", status="disabled"), device_id="device-1", now=now)

    with pytest.raises(ApiError, match="expired"):
        validate_invite_code_for_login(
            invite_code=InviteCode(code="A", status="active", expires_at=now - timedelta(seconds=1)),
            device_id="device-1",
            now=now,
        )

    with pytest.raises(ApiError, match="another device"):
        validate_invite_code_for_login(
            invite_code=InviteCode(code="A", status="active", bound_device_id="device-1"),
            device_id="device-2",
            now=now,
        )


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)


class FakeInviteCodeRepository:
    def __init__(self, *, existing: InviteCode | None = None) -> None:
        self.items: dict[str, InviteCode] = {}
        self.list_kwargs = {}
        if existing is not None:
            self.items[existing.code] = existing

    async def get_by_code(self, *, code: str, for_update: bool = False):
        return self.items.get(code)

    async def create(self, **kwargs):
        invite_code = InviteCode(id=uuid4(), status="active", used_count=0, **kwargs)
        self.items[invite_code.code] = invite_code
        return invite_code

    async def list_recent(self, *, limit: int = 50, offset: int = 0):
        self.list_kwargs = {"limit": limit, "offset": offset}
        return list(self.items.values())[:limit]

    async def count_all(self):
        return 123

    async def disable(self, *, invite_code, disabled_at):
        invite_code.status = "disabled"
        invite_code.disabled_at = disabled_at
        return invite_code
