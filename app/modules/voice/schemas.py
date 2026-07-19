from __future__ import annotations

from pydantic import BaseModel, Field


class SpeechTranscriptionResponse(BaseModel):
    text: str = Field(default="")
