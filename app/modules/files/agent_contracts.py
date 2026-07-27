from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


AgentFilePurpose = Literal["model_image", "model_file"]


class AgentFileResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_user_id: UUID
    file_id: UUID
    purpose: AgentFilePurpose


class AgentFileResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    file_id: UUID
    content_type: str
    original_filename: str
    model_url: str
    expires_at: datetime
