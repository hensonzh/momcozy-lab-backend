from __future__ import annotations


from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..baby.profile_schemas import BabySex, BabyFeedingMode


DeliveryMethod = Literal["vaginal", "cesarean", "assisted_vaginal", "other", "unknown"]
FeedingMode = Literal[
    "exclusive_breastfeeding",
    "expressed_milk_feeding",
    "mixed_feeding",
    "formula_feeding",
    "unknown",
]
InfantScope = Literal["current_delivery", "all"]
LactationMissingFieldCode = Literal[
    "mother_age_missing",
    "mother_delivery_count_missing",
    "mother_current_delivery_method_missing",
    "mother_actual_delivery_date_missing",
    "mother_cesarean_history_missing",
    "mother_postpartum_days_unavailable",
    "mother_current_feeding_mode_missing",
    "current_baby_profiles_missing",
    "infant_sex_missing",
    "infant_age_days_unavailable",
    "infant_age_months_unavailable",
    "infant_latest_measurement_missing",
]
LactationDataQualityIssueCode = Literal[
    "current_infants_not_selected",
    "actual_delivery_date_is_in_future",
    "infant_birth_date_mismatch",
    "infant_birth_date_is_in_future",
]


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActiveConcernContextOutput(_StrictOutputModel):
    issues: list[Literal["comfort", "feeding", "intake", "supply", "work", "other"]] = Field(min_length=1, max_length=6)
    note: str = Field(max_length=200)


class PersonalContextOutput(_StrictOutputModel):
    baby_count: int | None = Field(default=None, ge=1, le=3)
    gestation_weeks: int | None = Field(default=None, ge=20, le=45)
    gestation_days: int | None = Field(default=None, ge=0, le=6)
    feeding_methods: list[Literal["direct", "expressed", "formula"]] | None = Field(default=None, max_length=3)
    feeding_preference: Literal["breast", "formula", "mixed", "undecided"] | None = None
    caregivers: list[Literal["partner", "family", "professional", "self"]] | None = Field(default=None, max_length=4)
    return_to_work_date: date | None = None
    additional_context: str | None = Field(default=None, max_length=500)
    active_concerns: list[ActiveConcernContextOutput] = Field(default_factory=list, max_length=10)
    active_concern_count: int = Field(default=0, ge=0, le=100)


class LactationMotherContextOutput(_StrictOutputModel):
    """Compact maternal profile information relevant to the current milk supply assessment."""

    personal_context: PersonalContextOutput = Field(default_factory=PersonalContextOutput)
    preferred_name: str | None = Field(
        max_length=120,
        description="Mother’s preferred name; null when not recorded.",
    )
    age: int | None = Field(
        ge=12,
        le=70,
        description="Mother’s current age in completed years; null when unknown or unrecorded. Not inferred from delivery date.",
    )
    delivery_count: int | None = Field(
        ge=1,
        le=20,
        description="Total number of deliveries through this birth, not pregnancies or babies born this time; null when unknown.",
    )
    current_delivery_method: DeliveryMethod | None = Field(
        description=(
            "Delivery method for the current birth: vaginal, cesarean, assisted_vaginal, other, or explicitly recorded as unknown; null when unrecorded."
        ),
    )
    actual_delivery_date: date | None = Field(
        description="Actual date of the current birth, formatted YYYY-MM-DD; null when unrecorded.",
    )
    has_cesarean_history: bool | None = Field(
        description="Whether a cesarean occurred previously or during this delivery; true or false when confirmed, null otherwise.",
    )
    postpartum_days: int | None = Field(
        ge=0,
        description=(
            "Completed calendar days since delivery as of as_of_date; day of delivery is 0. Derived from actual_delivery_date; null when missing or after as_of_date."
        ),
    )
    current_feeding_mode: FeedingMode | None = Field(
        description=(
            "Current feeding mode: exclusive_breastfeeding, expressed_milk_feeding, mixed_feeding, formula_feeding, or explicitly recorded as unknown; null when unrecorded."
        ),
    )




class LatestInfantMeasurementOutput(_StrictOutputModel):
    """Latest valid growth measurement for this baby, not a historical trend."""

    weight_kg: float | None = Field(
        ge=0,
        description="Weight from the latest measurement, in kg; null if that record has no weight.",
    )
    height_cm: float | None = Field(
        ge=0,
        description="Length or height from the latest measurement, in cm; null if that record has neither.",
    )
    head_circumference_cm: float | None = Field(
        ge=0,
        description="Head circumference from the latest measurement, in cm; null if that record has none.",
    )
    recorded_on: date = Field(description="Measurement date; derived from the timestamp for legacy records, date-only for newer App records.")
    measured_at: datetime | None = Field(description="Legacy timezone-aware measurement time; null for newer date-only records. Do not infer midnight.")
    source: Literal["baby_records", "growth_records"] = Field(description="Record source; do not combine metrics across sources or measurement dates.")


class LactationInfantContextOutput(_StrictOutputModel):
    """Basic information about one baby and their relationship to the current delivery."""

    infant_id: UUID = Field(
        description="Stable baby profile UUID; required to update this baby’s profile or the current_infants relationship.",
    )
    name: str = Field(
        min_length=1,
        max_length=120,
        description="Name or nickname recorded in the baby profile.",
    )
    is_current_delivery: bool = Field(
        description="Whether this baby belongs to the mother’s current delivery; milk supply analysis uses only true values.",
    )
    birth_order: int | None = Field(
        ge=1,
        le=10,
        description=(
            "Birth order starting at 1 for babies from the current delivery; null for other babies."
        ),
    )
    feeding_mode: BabyFeedingMode
    sex: BabySex = Field(
        description=(
            "Sex recorded at birth: female, male, or unspecified; used only to select growth references."
        ),
    )
    birth_date: date | None = Field(
        description="Actual birth date of the baby in YYYY-MM-DD format; null when unrecorded.",
    )
    age_days: int | None = Field(
        ge=0,
        description=(
            "Completed age in days as of as_of_date; birth date is day 0. Prefer the mother’s actual delivery date; null when no usable date exists or it is in the future."
        ),
    )
    age_months: int | None = Field(
        ge=0,
        description=(
            "Completed calendar months of age as of as_of_date, not days divided by 30. Prefer the mother’s actual delivery date; null when no usable date exists or it is in the future."
        ),
    )
    latest_measurement: LatestInfantMeasurementOutput | None = Field(
        description="Latest valid length, weight, or head circumference measurement; null when no valid growth record exists.",
    )


class LactationMissingFieldOutput(_StrictOutputModel):
    """Profile fields needed for milk supply assessment that are currently unavailable."""

    code: LactationMissingFieldCode = Field(
        description=(
            "Stable machine-readable missing-field code: mother_* for maternal fields, current_baby_profiles_missing when no current baby profile can be read, infant_* for a baby identified by birth_order."
        ),
    )
    birth_order: int | None = Field(
        ge=1,
        le=10,
        description="Birth order of the affected baby; null for maternal fields or the current baby set as a whole.",
    )


class LactationDataQualityIssueOutput(_StrictOutputModel):
    """Nonblocking data quality issue that requires care during analysis."""

    code: LactationDataQualityIssueCode = Field(
        description=(
            "Stable machine-readable quality code: current_infants_not_selected for a multiple-baby delivery without current babies selected, actual_delivery_date_is_in_future for a future delivery date, infant_birth_date_mismatch for differing baby and maternal delivery dates, or infant_birth_date_is_in_future for a future baby birth date."
        ),
    )
    birth_order: int | None = Field(
        ge=1,
        le=10,
        description="Birth order of the affected baby; null for maternal or whole-set issues.",
    )


class MaternalBabyProfileReadOutput(_StrictOutputModel):
    """Compact maternal and baby profile returned by profile_read to the model."""

    as_of_date: date = Field(
        description="Trusted local reference date from the runtime in YYYY-MM-DD format; all postpartum days and baby ages are derived from it.",
    )
    infant_scope: InfantScope = Field(
        description=(
            "Scope of babies returned: current_delivery for babies from this delivery, all for every baby in the user’s profile. Milk supply analysis must use current_delivery."
        ),
    )
    mother: LactationMotherContextOutput = Field(
        description="Maternal profile information relevant to the current milk supply assessment.",
    )
    infants: list[LactationInfantContextOutput] = Field(
        description=(
            "Babies matching infant_scope, ordered by birth_order for current_delivery and by profile creation for all."
        ),
    )
    missing_fields: list[LactationMissingFieldOutput] = Field(
        description="Key fields unavailable in this context. Use stable codes, not display text.",
    )
    data_quality_issues: list[LactationDataQualityIssueOutput] = Field(
        description="Nonblocking quality issues found; empty when no known issues are present.",
    )
