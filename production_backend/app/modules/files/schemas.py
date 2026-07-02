from __future__ import annotations

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
