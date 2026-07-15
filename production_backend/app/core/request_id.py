from __future__ import annotations

import uuid


REQUEST_ID_HEADER = "X-Request-ID"


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def normalize_request_id(value: str | None) -> str:
    request_id = str(value or "").strip()
    return request_id or new_request_id()
