from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StoolPhotoRead(BaseModel):
    photo_id: UUID = Field(validation_alias="id")
    review_id: UUID = Field(validation_alias="id")
    review_status: Literal["pending", "reviewing", "passed", "rejected", "inconclusive", "failed"]
    reason_codes: list[str]
    policy_version: str | None
    reviewed_at: datetime | None
    review_expires_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
