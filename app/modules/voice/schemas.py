from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SpeechTranscriptionResponse(BaseModel):
    text: str = Field(default="")


class PrivateSpeechSynthesisRequest(BaseModel):
    """Keep private assistant text in the authenticated request body, not URLs."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def non_blank_text(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("Text is required.")
        return text
