from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict


class CareConversationLinkWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    share_from_now: Literal[True]


class CareConversationLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    thread_id: UUID
    episode_id: UUID
    shared_since: AwareDatetime


class CareConversationLinksRead(BaseModel):
    items: list[CareConversationLinkRead]
