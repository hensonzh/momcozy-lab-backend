from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class CareRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("*", mode="before")
    @classmethod
    def utc_database_values(cls, value: Any) -> Any:
        return value.replace(tzinfo=timezone.utc) if isinstance(value, datetime) and value.tzinfo is None else value


class ServicePackageRead(BaseModel):
    id: str
    name: str
    subtitle: str
    description: str
    duration_days: int
    sessions: int
    price_minor: int
    currency: Literal["USD"] = "USD"
    highlights: list[str]
    expert_services: list[str]
    continuous_services: list[str]


class CareProviderRead(CareRead):
    user_id: UUID
    display_name: str
    timezone: str
    regions: list[str]
    languages: list[str]
    bio: str
    sandbox: bool


class ServiceCatalog(BaseModel):
    packages: list[ServicePackageRead]
    providers: list[CareProviderRead]
    available_regions: list[str]
    payment_mode: Literal["sandbox", "disabled", "stripe"]


class EligibilityWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    package_id: str = Field(min_length=1, max_length=64)
    region: str = Field(pattern=r"^[A-Z]{2}$")
    acknowledges_non_emergency: Literal[True]


class EligibilityRead(CareRead):
    id: UUID
    package_id: str
    region: str
    eligible: bool
    expires_at: AwareDatetime
    reason: str


class OrderWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligibility_id: UUID


OrderStatus = Literal["pending", "processing", "requires_action", "reconciling", "paid", "failed", "cancelled"]
EpisodeStatus = Literal["provisioning_pending", "active", "paused", "completed", "cancelled"]
CareStage = Literal["preparation", "initial_consultation", "active_care", "follow_up", "conclusion"]


class CareOrderRead(CareRead):
    id: UUID
    package_id: str
    status: OrderStatus
    price_minor: int
    duration_days: int
    total_sessions: int
    currency: Literal["USD"]
    payment_mode: Literal["sandbox", "stripe"]
    stripe_livemode: bool | None = None
    region: str
    version: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CareEpisodeRead(CareRead):
    id: UUID
    order_id: UUID
    package_id: str
    baby_id: UUID | None
    assigned_ibclc_id: UUID | None
    status: EpisodeStatus
    stage: CareStage
    total_sessions: int
    remaining_sessions: int
    starts_at: AwareDatetime | None
    ends_at: AwareDatetime | None
    version: int


class CareOverview(BaseModel):
    orders: list[CareOrderRead]
    episodes: list[CareEpisodeRead]


class SandboxPaymentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    outcome: Literal["succeeded", "declined", "requires_action", "reconciling", "cancelled"]


class PurchaseRead(BaseModel):
    order: CareOrderRead
    episode: CareEpisodeRead | None = None


class CheckoutRead(BaseModel):
    url: str | None
    purchase: PurchaseRead
