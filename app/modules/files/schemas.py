from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FileRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    status: str

    model_config = ConfigDict(from_attributes=True)


class FileListResponse(BaseModel):
    items: list[FileRead]
    next_cursor: str | None = None


class FileVisionEventRead(BaseModel):
    type: str
    sequence: int
    file_id: UUID
    payload: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)
