from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

BabySex = Literal['female', 'male', 'unspecified']
BabyFeedingMode = Literal['exclusive_breastfeeding', 'expressed_milk_feeding', 'mixed_feeding', 'formula_feeding', 'unknown']


class BabyProfileContent(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, from_attributes=True)
    name: str = Field(min_length=1, max_length=120)
    birth_date: date | None = None
    sex: BabySex = 'unspecified'
    feeding_mode: BabyFeedingMode = 'unknown'


class BabyProfileWrite(BabyProfileContent):
    timezone: str = Field(min_length=1, max_length=80)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError('Use an IANA timezone.') from error
        return value


class BabyProfileUpdate(BabyProfileWrite):
    expected_version: int = Field(ge=1)


class BabyProfileRead(BabyProfileContent):
    id: UUID
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class BabyProfileList(BaseModel):
    items: list[BabyProfileRead]
