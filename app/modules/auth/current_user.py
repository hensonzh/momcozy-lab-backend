from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class CurrentUser:
    user_id: UUID
    subject: str
    session_id: str
    token_id: str
    roles: frozenset[str]
    permissions: frozenset[str]
