from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CryAnalysisRead(BaseModel):
    id: UUID
    status: Literal['pending', 'done', 'incomplete', 'failed']
    answer: Literal['sleep', 'pain', 'burp', 'uncomfortable', 'hungry', 'no_cry'] | None
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CryAnalysisListRead(BaseModel):
    items: list[CryAnalysisRead]


class CryAnalysisResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    actor_user_id: UUID
    thread_id: UUID


class CryAnalysisInternalRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: Literal['done', 'incomplete']
    answer: Literal['sleep', 'pain', 'burp', 'uncomfortable', 'hungry', 'no_cry'] | None
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    created_at: datetime
    thread_id: UUID
