from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from ...core.errors import ApiError
from .current_user import CurrentUser


ADMIN_ROLE = "admin"


class PermissionPolicy:
    def has_permission(self, user: CurrentUser, permission: str) -> bool:
        return ADMIN_ROLE in user.roles or permission in user.permissions

    def has_any_permission(self, user: CurrentUser, permissions: Iterable[str]) -> bool:
        return ADMIN_ROLE in user.roles or any(permission in user.permissions for permission in permissions)

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
