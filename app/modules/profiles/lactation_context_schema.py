from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


DeliveryMethod = Literal["vaginal", "cesarean", "assisted_vaginal", "other", "unknown"]
FeedingMode = Literal[
    "exclusive_breastfeeding",
    "expressed_milk_feeding",
    "mixed_feeding",
    "formula_feeding",
    "unknown",
]
SexAtBirth = Literal["female", "male", "intersex", "unknown", "undisclosed"]
LactationMissingFieldCode = Literal[
    "mother_age_missing",
    "mother_delivery_count_missing",
    "mother_current_delivery_method_missing",
    "mother_actual_delivery_date_missing",
    "mother_cesarean_history_missing",
    "mother_postpartum_days_unavailable",
    "mother_current_feeding_mode_missing",
    "current_infant_profiles_missing",
    "infant_sex_at_birth_missing",
    "infant_age_days_unavailable",
    "infant_age_months_unavailable",
    "infant_birth_weight_missing",
    "infant_gestational_age_missing",
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


class LactationMotherContextOutput(_StrictOutputModel):
    """影响当前奶量分析的妈妈侧紧凑基础信息。"""

    age: int | None = Field(
        ge=12,
        le=70,
        description="妈妈登记的当前周岁，单位为岁；未知或未登记时为 null，不由分娩日期推算。",
    )
    delivery_count: int | None = Field(
        ge=1,
        le=20,
        description="截至当前这次分娩的累计分娩次数；不是妊娠次数，也不是本次出生的宝宝数量；未知时为 null。",
    )
    current_delivery_method: DeliveryMethod | None = Field(
        description=(
            "当前这次分娩方式；vaginal=阴道分娩，cesarean=剖宫产，"
            "assisted_vaginal=助产阴道分娩，other=其他，unknown=已明确记录为未知；未登记时为 null。"
        ),
    )
    actual_delivery_date: date | None = Field(
        description="当前这次实际分娩日期，格式为 YYYY-MM-DD；未登记时为 null。",
    )
    has_cesarean_history: bool | None = Field(
        description="当前这次分娩以前或本次是否曾发生过剖宫产；true=有，false=没有，尚未确认时为 null。",
    )
    postpartum_days: int | None = Field(
        ge=0,
        description=(
            "截至 as_of_date 的产后完整日历天数，单位为天，分娩当天为 0；"
            "由 actual_delivery_date 派生，日期缺失或晚于 as_of_date 时为 null。"
        ),
    )
    current_feeding_mode: FeedingMode | None = Field(
        description=(
            "当前喂养模式；exclusive_breastfeeding=纯亲喂母乳，expressed_milk_feeding=瓶喂挤出母乳，"
            "mixed_feeding=混合喂养，formula_feeding=配方奶喂养，unknown=已明确记录为未知；未登记时为 null。"
        ),
    )


class GestationalAgeAtBirthOutput(_StrictOutputModel):
    """同一出生孕周的天数表示和便于模型理解的拆分表示。"""

    total_days: int = Field(
        ge=140,
        le=315,
        description="出生孕周的持久化标准值，单位为孕天，总范围为 140 至 315 天。",
    )
    weeks: int = Field(
        ge=20,
        le=45,
        description="由 total_days 整除 7 派生的完整孕周数，单位为周。",
    )
    days: int = Field(
        ge=0,
        le=6,
        description="由 total_days 对 7 取余派生的额外孕天，单位为天，范围为 0 至 6。",
    )
    is_preterm: bool = Field(
        description="是否早产；由 total_days 是否小于 259 天（37 周）派生。",
    )


class LatestInfantMeasurementOutput(_StrictOutputModel):
    """宝宝最近一条有效生长测量记录；不是历史趋势。"""

    weight_kg: float | None = Field(
        ge=0,
        description="最近一次测量中的体重，单位为 kg；该次记录未测体重时为 null。",
    )
    height_cm: float | None = Field(
        ge=0,
        description="最近一次测量中的身长或身高，单位为 cm；该次记录未测身长或身高时为 null。",
    )
    head_circumference_cm: float | None = Field(
        ge=0,
        description="最近一次测量中的头围，单位为 cm；该次记录未测头围时为 null。",
    )
    measured_at: datetime = Field(
        description="最近一次有效生长记录的测量时间，为带时区偏移的 ISO 8601 日期时间。",
    )

    @field_serializer("measured_at")
    def serialize_measured_at(self, value: datetime) -> str:
        return value.isoformat()


class LactationInfantContextOutput(_StrictOutputModel):
    """当前这次分娩中一个宝宝的奶量分析基础信息。"""

    birth_order: int = Field(
        ge=1,
        le=10,
        description="宝宝在当前这次多宝宝分娩中的出生顺序，从 1 开始；用于稳定区分每个宝宝。",
    )
    sex_at_birth: SexAtBirth | None = Field(
        description=(
            "宝宝出生时登记的生理性别，不表示性别认同；female=女，male=男，intersex=间性，"
            "unknown=已明确记录为未知，undisclosed=不披露；未登记时为 null。"
        ),
    )
    age_days: int | None = Field(
        ge=0,
        description=(
            "截至 as_of_date 的完整日龄，单位为天，出生当天为 0；"
            "优先由妈妈当前实际分娩日期派生，缺少可用日期或日期在未来时为 null。"
        ),
    )
    age_months: int | None = Field(
        ge=0,
        description=(
            "截至 as_of_date 已完成的完整日历月龄，单位为月，不按天数除以 30；"
            "优先由妈妈当前实际分娩日期派生，缺少可用日期或日期在未来时为 null。"
        ),
    )
    birth_weight_kg: float | None = Field(
        ge=0.2,
        le=10,
        description="宝宝出生体重，单位为 kg；未登记时为 null。",
    )
    gestational_age_at_birth: GestationalAgeAtBirthOutput | None = Field(
        description="宝宝出生孕周的派生展示；底层总孕天未登记时为 null。",
    )
    latest_measurement: LatestInfantMeasurementOutput | None = Field(
        description="宝宝最近一条有效身高、体重或头围测量；没有有效生长记录时为 null。",
    )


class LactationMissingFieldOutput(_StrictOutputModel):
    """奶量分析所需但当前不可用的字段。"""

    code: LactationMissingFieldCode = Field(
        description=(
            "稳定、可机读的缺失字段代码；mother_* 表示妈妈字段，current_infant_profiles_missing 表示"
            "没有可读取的当前宝宝档案，infant_* 表示 birth_order 指定宝宝的字段。"
        ),
    )
    birth_order: int | None = Field(
        ge=1,
        le=10,
        description="缺失项属于某个宝宝时为其出生顺序；属于妈妈或整个当前宝宝集合时为 null。",
    )


class LactationDataQualityIssueOutput(_StrictOutputModel):
    """不阻断读取、但分析时需要谨慎处理的数据质量问题。"""

    code: LactationDataQualityIssueCode = Field(
        description=(
            "稳定、可机读的数据质量代码；current_infants_not_selected=多宝宝档案尚未标记本次分娩宝宝，"
            "actual_delivery_date_is_in_future=实际分娩日期在未来，infant_birth_date_mismatch=宝宝出生日期"
            "与妈妈实际分娩日期不一致，infant_birth_date_is_in_future=宝宝出生日期在未来。"
        ),
    )
    birth_order: int | None = Field(
        ge=1,
        le=10,
        description="问题属于某个宝宝时为其出生顺序；属于妈妈或当前宝宝集合时为 null。",
    )


class LactationContextReadOutput(_StrictOutputModel):
    """lactation_context_read 返回给模型的完整、紧凑、数据库支持的母婴上下文。"""

    as_of_date: date = Field(
        description="可信运行时提供的本地基准日期，格式为 YYYY-MM-DD；所有产后天数和宝宝年龄均以此日期派生。",
    )
    mother: LactationMotherContextOutput = Field(
        description="影响当前奶量分析的妈妈侧基础信息。",
    )
    infants: list[LactationInfantContextOutput] = Field(
        max_length=10,
        description="当前这次分娩的宝宝列表，按 birth_order 升序返回；没有可用当前宝宝时为空数组。",
    )
    missing_fields: list[LactationMissingFieldOutput] = Field(
        description="当前上下文中不可用的关键字段；使用稳定 code，不解析展示文本。",
    )
    data_quality_issues: list[LactationDataQualityIssueOutput] = Field(
        description="已发现但不阻断读取的数据质量问题；空数组表示未发现已知问题。",
    )
