from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.auth import CurrentUser, PermissionPolicy


def test_permission_policy_allows_explicit_permission() -> None:
    user = _user(permissions={"files:write"})

    PermissionPolicy().require_permission(user, "files:write")


def test_permission_policy_allows_admin_role() -> None:
    user = _user(roles={"admin"})

    PermissionPolicy().require_permission(user, "any:permission")


def test_permission_policy_denies_missing_permission() -> None:
    user = _user()

    with pytest.raises(ApiError, match="Permission denied"):
        PermissionPolicy().require_permission(user, "files:write")


def test_permission_policy_allows_owner_scope() -> None:
    user_id = uuid4()
    user = _user(user_id=user_id)

    PermissionPolicy().require_owner_scope(user, owner_user_id=user_id)


def test_permission_policy_denies_cross_owner_scope_without_permission() -> None:
    user = _user()

    with pytest.raises(ApiError) as exc_info:
        PermissionPolicy().require_owner_scope(user, owner_user_id=uuid4(), permission="files:admin")

    assert exc_info.value.code == "owner_scope_violation"


def test_permission_policy_allows_cross_owner_scope_with_permission() -> None:
    user = _user(permissions={"files:admin"})

    PermissionPolicy().require_owner_scope(user, owner_user_id=uuid4(), permission="files:admin")


def _user(
    *,
    user_id=None,
    roles: set[str] | None = None,
    permissions: set[str] | None = None,
) -> CurrentUser:
    actual_user_id = user_id or uuid4()
    return CurrentUser(
        user_id=actual_user_id,
        subject=str(actual_user_id),
        session_id="session",
        token_id="token",
        roles=frozenset(roles or {"user"}),
        permissions=frozenset(permissions or set()),
    )
