from __future__ import annotations

from datetime import datetime

from ..users.models import AccountStatus
from pydantic import BaseModel, ConfigDict, Field


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    device_id: str = Field(default="", max_length=120)


class EmailRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    # Old clients may send a password; it is not stored before mailbox proof.
    password: str | None = Field(default=None, min_length=8, max_length=128)


class RegistrationCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    token: str = Field(min_length=1, max_length=256)


class EmailChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str | None = Field(default=None, min_length=8, max_length=128)
    email: str = Field(min_length=3, max_length=320)
    token: str = Field(min_length=1, max_length=256)
    device_id: str = Field(default="", max_length=120)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


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


class AccountProfile(BaseModel):
    id: str
    email: str | None = None
    email_verified: bool
    account_status: AccountStatus
    auth_providers: list[str]
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: TokenUser


class LogoutResponse(BaseModel):
    status: str = "ok"


class AccountOperationResponse(BaseModel):
    status: str
