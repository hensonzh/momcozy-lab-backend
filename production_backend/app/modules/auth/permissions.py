from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from ...core.errors import ApiError
from .current_user import CurrentUser


ADMIN_ROLE = "admin"
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "user": frozenset(
        {
            "profile:read:self",
            "business_context:read:self",
            "files:read:self",
            "diary:write:self",
            "memory:write:self",
            "plans:write:self",
            "notifications:create:self",
            "records:write:self",
            "hospital_bag_cart:update:self",
            "support_ticket:create:self",
        }
    )
}


class PermissionPolicy:
    def has_permission(self, user: CurrentUser, permission: str) -> bool:
        return ADMIN_ROLE in user.roles or permission in user.permissions or permission in _role_permissions(user.roles)

    def has_any_permission(self, user: CurrentUser, permissions: Iterable[str]) -> bool:
        role_permissions = _role_permissions(user.roles)
        return ADMIN_ROLE in user.roles or any(permission in user.permissions or permission in role_permissions for permission in permissions)

    def require_permission(self, user: CurrentUser, permission: str) -> None:
        if not self.has_permission(user, permission):
            raise ApiError(code="permission_denied", message="Permission denied.", status=403)

    def require_any_permission(self, user: CurrentUser, permissions: Iterable[str]) -> None:
        if not self.has_any_permission(user, permissions):
            raise ApiError(code="permission_denied", message="Permission denied.", status=403)

    def require_owner_scope(
        self,
        user: CurrentUser,
        *,
        owner_user_id: UUID,
        permission: str | None = None,
    ) -> None:
        if user.user_id == owner_user_id:
            return
        if permission and self.has_permission(user, permission):
            return
        raise ApiError(code="owner_scope_violation", message="Resource is outside the current user scope.", status=403)


def _role_permissions(roles: frozenset[str]) -> frozenset[str]:
    permissions: set[str] = set()
    for role in roles:
        permissions.update(ROLE_PERMISSIONS.get(role, frozenset()))
    return frozenset(permissions)
