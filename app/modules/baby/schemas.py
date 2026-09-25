from __future__ import annotations

from typing import Annotated, Literal
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, computed_field, field_validator, model_validator

RecordKind = Literal['feeding', 'sleep', 'diaper', 'growth', 'development', 'daily_status']
DevelopmentItem = Literal['looks-at-face', 'responds-to-sound', 'lifts-head']
DEVELOPMENT_LABELS = {'looks-at-face': 'Looks at a face up close', 'responds-to-sound': 'Reacts to sounds with movement or expressions', 'lifts-head': 'Briefly lifts their head during tummy time'}


class ObservationBase(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class NotedObservation(ObservationBase):
    occurred_at: AwareDatetime
    note: str = Field(default='', max_length=2000)


class DatedObservation(ObservationBase):
    recorded_on: date
    timezone: str = Field(min_length=1, max_length=80)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError('Use an IANA timezone.') from error
        return value


class FeedingObservation(NotedObservation):
    kind: Literal['feeding']
    method: Literal['breastfeeding', 'expressed_milk', 'formula']
    side: Literal['left', 'right', 'both'] | None = None
    volume_ml: float | None = Field(default=None, gt=0, le=1000)
    duration_minutes: int | None = Field(default=None, gt=0, le=240)

    @model_validator(mode='after')
    def measurement(self) -> FeedingObservation:
        if self.method == 'breastfeeding':
            if self.side is None or self.volume_ml is not None:
                raise ValueError('Breastfeeding requires a side and cannot record measured intake.')
        elif self.side is not None or self.duration_minutes is not None:
            raise ValueError('Bottle feeding cannot contain breastfeeding fields.')
        return self


class SleepObservation(NotedObservation):
    kind: Literal['sleep']
    ended_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def ordered(self) -> SleepObservation:
        if self.ended_at is not None and self.ended_at <= self.occurred_at:
            raise ValueError('Wake time must be later than sleep start.')
        return self


class DiaperObservation(NotedObservation):
    kind: Literal['diaper']
    diaper_kind: Literal['wet', 'dirty', 'both']
    color: Literal['yellow', 'yellow_brown', 'green', 'brown', 'black', 'red', 'pale', 'unsure'] | None = None
    consistency: Literal['watery', 'loose', 'pasty', 'formed', 'hard', 'unsure'] | None = None
    signs: list[Literal['blood', 'mucus']] = Field(default_factory=list, max_length=2)

    @model_validator(mode='after')
    def stool_fields(self) -> DiaperObservation:
        if self.diaper_kind == 'wet' and (self.color is not None or self.consistency is not None or self.signs):
            raise ValueError('Wet-only records cannot contain stool observations.')
        if len(set(self.signs)) != len(self.signs):
            raise ValueError('Visible signs must be unique.')
        self.signs.sort()
        return self


class DailyStatusObservation(DatedObservation):
    kind: Literal['daily_status']
    mental_state: Literal['content', 'active', 'crying', 'drowsy'] | None = None
    wet_count: int | None = Field(default=None, ge=1, le=100, strict=True)
    stool_count: int | None = Field(default=None, ge=1, le=100, strict=True)
    color: Literal['yellow', 'yellow_brown', 'green', 'brown', 'black', 'red', 'pale', 'unsure'] | None = None
    consistency: Literal['watery', 'loose', 'pasty', 'formed', 'hard', 'unsure'] | None = None

    @model_validator(mode='after')
    def filled_tabs(self) -> DailyStatusObservation:
        if self.mental_state is None and self.wet_count is None and self.stool_count is None:
            raise ValueError('Fill at least one daily status item.')
        if self.stool_count is None and (self.color is not None or self.consistency is not None):
            raise ValueError('Stool observations require the daily stool count.')
        return self


class GrowthObservation(DatedObservation):
    measurement_source: Literal['home','clinic','other'] | None = None
    kind: Literal['growth']
    metric: Literal['weight', 'length', 'head_circumference']
    value: float = Field(gt=0, le=150)

    @model_validator(mode='after')
    def metric_range(self) -> GrowthObservation:
        if self.metric == 'weight' and self.value > 50:
            raise ValueError('Check the weight and unit.')
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unit(self) -> Literal['kg', 'cm']:
        return 'kg' if self.metric == 'weight' else 'cm'


class DevelopmentObservation(DatedObservation):
    kind: Literal['development']
    item_id: DevelopmentItem
    status: Literal['observed', 'not_observed', 'unsure']

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label(self) -> str:
        return DEVELOPMENT_LABELS[self.item_id]


Observation = Annotated[FeedingObservation | SleepObservation | DiaperObservation | GrowthObservation | DevelopmentObservation | DailyStatusObservation, Field(discriminator='kind')]
observation_adapter: TypeAdapter[Observation] = TypeAdapter(Observation)


class BabyRecordWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    observation: Observation


class BabyRecordBatchWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    observations: list[Annotated[GrowthObservation | DevelopmentObservation, Field(discriminator='kind')]] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def one_form(self) -> BabyRecordBatchWrite:
        if len({(item.kind, item.recorded_on, item.timezone) for item in self.observations}) != 1:
            raise ValueError('A batch must share a category, date and recording timezone.')
        keys = [item.metric if isinstance(item, GrowthObservation) else item.item_id for item in self.observations]
        if len(set(keys)) != len(keys):
            raise ValueError('Record each measurement or observation once.')
        return self


class BabyRecordUpdate(BabyRecordWrite):
    expected_version: int = Field(ge=1)


class BabyRecordVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)


class BabyRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    baby_id: UUID
    version: int
    observation: Observation
    deleted_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class BabyRecordList(BaseModel):
    items: list[BabyRecordRead]
    total: int
    offset: int
    limit: int
    server_time: AwareDatetime


class BabyRecordDeletion(BaseModel):
    id: UUID
    version: int


class BabyRecordItems(BaseModel):
    items: list[BabyRecordRead]
