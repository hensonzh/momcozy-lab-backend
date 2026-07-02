from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorEnvelope:
    code: str
    message: str
    status: int
    request_id: str | None = None
    details: dict[str, Any] | None = None

