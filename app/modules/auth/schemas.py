from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    device_id: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    device_id: str = Field(default="", max_length=120)


class InviteLoginRequest(BaseModel):
    invite_code: str = Field(min_length=1, max_length=120)
    device_id: str = Field(min_length=1, max_length=120)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class TokenUser(BaseModel):
    id: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: TokenUser


class LogoutResponse(BaseModel):
    status: str = "ok"
