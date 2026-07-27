from __future__ import annotations

from datetime import date as DateValue
from datetime import datetime as DateTimeValue
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


MilkAnalysisOperation = Literal["review", "start_or_resume", "answer", "evaluate"]
MilkAnalysisDetailLevel = Literal["summary", "detailed"]


class MilkAnalysisWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(ge=1, le=30, description="本次状态或分析快照覆盖的近期自然日数。")
    limit: int = Field(ge=1, le=20, description="快照最多展示的每类近期明细数量。")
    include_today: bool = Field(description="统计窗口是否包含当前自然日。")


class MilkAnalysisStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_coverage: str = Field(description="数据覆盖情况，例如 ready、limited 或 no_recent_data。")
    pumping_trend: str = Field(description="妈妈侧有测量值的吸奶产出趋势，例如 increasing、stable 或 decreasing。")
    measured_only: bool = Field(description="趋势计算是否只使用具有明确测量值的实际吸奶记录。")


class MilkAnalysisCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infants: int = Field(ge=0, description="当前用户资料中的宝宝数量。")
    recent_feedings: int = Field(ge=0, description="本次快照读取到的近期宝宝喂养实际记录数。")
    recent_pumpings: int = Field(ge=0, description="本次快照读取到的近期妈妈吸奶实际记录数。")
    trend_days: int = Field(ge=0, description="吸奶趋势返回的自然日数量。")
    days_with_pumping: int = Field(ge=0, description="趋势窗口中至少有一次吸奶记录的自然日数量。")
    trend_pumping_count: int = Field(ge=0, description="趋势窗口内具有测量值的吸奶总次数。")
    recent_growth: int | None = Field(
        default=None,
        ge=0,
        description="详细分析读取到的近期宝宝生长记录数；摘要模式为 null 或不返回。",
    )


class MilkAnalysisVolumes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recent_feeding_volume_ml: float = Field(ge=0, description="近期宝宝喂养记录中明确记录的摄入量合计，单位 ml。")
    recent_pumped_volume_ml: float = Field(ge=0, description="近期妈妈吸奶明细中明确记录的产出量合计，单位 ml。")
    trend_pumped_volume_ml: float = Field(ge=0, description="趋势窗口内妈妈吸奶产出量合计，单位 ml。")
    average_daily_pumped_volume_ml: float = Field(
        ge=0,
        description="趋势窗口内按窗口天数计算的妈妈日均吸奶产出量，单位 ml。",
    )


class MilkAnalysisLatestEvents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feeding_at: DateTimeValue | None = Field(default=None, description="最近一次宝宝喂养实际发生时间；没有记录时为 null。")
    pumping_at: DateTimeValue | None = Field(default=None, description="最近一次妈妈吸奶实际发生时间；没有记录时为 null。")


class MilkAnalysisFeedingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(description="宝宝喂养实际记录的稳定 UUID。")
    infant_id: UUID | None = Field(default=None, description="该次喂养对应的宝宝 UUID；未指定时为 null。")
    feed_time: DateTimeValue = Field(description="该次喂养实际发生时间。")
    feed_type: str = Field(description="喂养方式。")
    feed_action: str = Field(description="喂养动作。")
    volume_ml: float | None = Field(default=None, ge=0, description="明确记录的宝宝摄入量，单位 ml；未知时为 null。")
    duration_seconds: int | None = Field(
        default=None,
        ge=0,
        description="该次喂养持续时间，单位秒；未知时为 null。",
    )
    title: str = Field(description="该次喂养记录标题。")


class MilkAnalysisPumpingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(description="妈妈吸奶实际记录的稳定 UUID。")
    pump_start_time: DateTimeValue = Field(description="该次吸奶实际开始时间。")
    pump_end_time: DateTimeValue | None = Field(default=None, description="该次吸奶实际结束时间；未知时为 null。")
    milk_volume_ml: float | None = Field(default=None, ge=0, description="明确记录的吸奶产出量，单位 ml；未知时为 null。")
    duration_seconds: int | None = Field(
        default=None,
        ge=0,
        description="该次吸奶持续时间，单位秒；未知时为 null。",
    )
    pump_type: str = Field(description="吸奶方式或设备类型。")
    source: str = Field(description="吸奶记录来源，例如 manual、agent 或 device。")
    title: str = Field(description="该次吸奶记录标题。")


class MilkAnalysisGrowthRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(description="宝宝生长测量记录的稳定 UUID。")
    infant_id: UUID | None = Field(default=None, description="该次测量对应的宝宝 UUID；未指定时为 null。")
    measured_at: DateTimeValue = Field(description="身高、体重或头围的实际测量时间。")
    height_cm: float | None = Field(default=None, ge=0, description="宝宝身高，单位 cm；未测量时为 null。")
    weight_kg: float | None = Field(default=None, ge=0, description="宝宝体重，单位 kg；未测量时为 null。")
    head_cm: float | None = Field(default=None, ge=0, description="宝宝头围，单位 cm；未测量时为 null。")


class MilkAnalysisTrendDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: DateValue = Field(description="该条吸奶趋势统计对应的自然日。")
    pumped_milk_volume_ml: float = Field(ge=0, description="该日具有测量值的妈妈吸奶产出量合计，单位 ml。")
    pumping_count: int = Field(ge=0, description="该日具有测量值的吸奶次数。")
    measured_only: bool = Field(description="该日统计是否只包含具有明确奶量测量值的实际记录。")


class MilkAnalysisPumpingRhythm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(description="代表性吸奶时刻采用的 IANA 时区。")
    representative_date: DateValue | None = Field(
        default=None,
        description="用于提取代表性吸奶时刻的记录日期；没有吸奶记录时为 null。",
    )
    representative_times: list[str] = Field(description="代表性日期内去重并排序后的本地吸奶时刻，格式 HH:MM。")


class MilkAnalysisInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pathway: str = Field(description="基于当前确定性事实建议进入的分析路径。")
    data_coverage: str = Field(description="用于生成分析路径的数据覆盖情况。")
    pumping_trend: str = Field(description="用于生成分析路径的妈妈侧吸奶产出趋势。")
    has_recent_growth: bool = Field(description="当前快照是否包含至少一条近期宝宝生长记录。")
    missing_inputs: list[str] = Field(description="当前分析仍缺失或需要留意的输入信号代码。")
    recommended_next_step: str = Field(description="基于当前快照建议的下一步采集、判断或安全支持动作。")


class MilkAnalysisReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail_level: MilkAnalysisDetailLevel = Field(description="本次返回的是状态摘要还是包含明细的详细分析快照。")
    window: MilkAnalysisWindow = Field(description="本次快照使用的统计窗口。")
    status: MilkAnalysisStatus = Field(description="根据实际记录确定性计算出的数据覆盖和吸奶趋势状态。")
    counts: MilkAnalysisCounts = Field(description="当前快照包含的宝宝、记录和趋势计数。")
    volumes: MilkAnalysisVolumes = Field(description="宝宝侧摄入和妈妈侧吸奶产出的确定性汇总。")
    latest: MilkAnalysisLatestEvents = Field(description="最近一次喂养和吸奶的实际发生时间。")
    observation_flags: list[str] = Field(description="当前资料或实际记录中缺失信息的稳定提示代码。")
    recent_feedings: list[MilkAnalysisFeedingRecord] | None = Field(
        default=None,
        description="详细模式返回的近期宝宝喂养实际记录；摘要模式为 null 或不返回。",
    )
    recent_pumpings: list[MilkAnalysisPumpingRecord] | None = Field(
        default=None,
        description="详细模式返回的近期妈妈吸奶实际记录；摘要模式为 null 或不返回。",
    )
    pumping_rhythm: MilkAnalysisPumpingRhythm | None = Field(
        default=None,
        description="详细模式从完整窗口实际吸奶记录提取的代表性节奏；摘要模式为 null 或不返回。",
    )
    recent_growth: list[MilkAnalysisGrowthRecord] | None = Field(
        default=None,
        description="详细模式返回的近期宝宝生长测量记录；摘要模式为 null 或不返回。",
    )
    pumping_trends: list[MilkAnalysisTrendDay] | None = Field(
        default=None,
        description="详细模式返回的逐日吸奶产出趋势；摘要模式为 null 或不返回。",
    )
    analysis: MilkAnalysisInterpretation | None = Field(
        default=None,
        description="详细模式基于确定性快照生成的分析路径和下一步；摘要模式为 null 或不返回。",
    )


class MilkAnalysisProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1, description="六项奶量分析采集当前所在的序号。")
    total: int = Field(ge=1, description="奶量分析需要采集的总项目数。")
    completed_count: int = Field(ge=0, description="已经获得可信用户回答的采集项目数。")
    remaining_count: int = Field(ge=0, description="仍需获得可信用户回答的采集项目数。")


class MilkAnalysisWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_state_id: UUID = Field(description="当前线程持久化奶量分析采集状态的稳定 UUID。")
    workflow_phase: str = Field(description="当前奶量分析工作流阶段。")
    current_field: str | None = Field(default=None, description="当前等待用户回答的采集字段；采集完成时为 null。")
    next_question: str = Field(description="需要直接展示给用户的下一条问题；无需继续提问时为空字符串。")
    progress: MilkAnalysisProgress = Field(description="六项奶量分析采集的完成进度。")
    can_evaluate: bool = Field(description="当前已采集信息是否足以执行确定性评估。")


class MilkAnalysisEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_state_id: UUID = Field(description="本次评估对应的持久化奶量分析工作流状态 UUID。")
    artifact_id: UUID = Field(description="生成或复用的奶量分析卡片 UUID。")
    artifact_type: str = Field(description="生成的分析产物类型，当前为 milk_analysis_card。")
    maternal_red_flags: bool = Field(description="当前回答是否包含需要优先处理的妈妈乳房或全身危险信号。")
    infant_intake_risk: bool = Field(description="当前回答是否包含需要优先确认的宝宝摄入或生长风险信号。")
    data_coverage: str = Field(description="本次综合评估使用的近期记录覆盖情况。")
    pumping_trend: str = Field(description="本次综合评估识别出的妈妈侧吸奶产出趋势。")
    replayed: bool = Field(description="本次结果是否复用了当前工作流已经生成的评估卡片。")


class MilkAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="本次分析操作的稳定结果状态。")
    operation: MilkAnalysisOperation = Field(description="本次执行的奶量分析操作。")
    review: MilkAnalysisReview | None = Field(
        default=None,
        description="operation=review 时返回的确定性状态或详细快照；其他操作为 null 或不返回。",
    )
    workflow: MilkAnalysisWorkflow | None = Field(
        default=None,
        description="开始、恢复或回答采集问题后返回的持久化工作流进度；其他操作为 null 或不返回。",
    )
    evaluation: MilkAnalysisEvaluation | None = Field(
        default=None,
        description="operation=evaluate 时返回的分析卡片和风险、趋势结论；其他操作为 null 或不返回。",
    )
