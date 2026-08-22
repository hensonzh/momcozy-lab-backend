from __future__ import annotations


AGENT_RUN_PERMISSION = "agent:run"
RUNTIME_TOKEN_PERMISSIONS_OPENAPI_FIELD = (
    "x-momcozy-runtime-token-permissions"
)

STANDARD_USER_PERMISSIONS = frozenset(
    {
        AGENT_RUN_PERMISSION,
        "device:read",
        "diary:read",
        "diary:write",
        "files:read",
        "plans:read",
        "plans:write",
        "prenatal:read",
        "prenatal:write",
        "profile:read",
        "profile:write",
        "records:read",
        "records:write",
        "support:write",
    }
)


__all__ = [
    "AGENT_RUN_PERMISSION",
    "RUNTIME_TOKEN_PERMISSIONS_OPENAPI_FIELD",
    "STANDARD_USER_PERMISSIONS",
]
