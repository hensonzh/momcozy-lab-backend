from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .schedule_domain import ScheduleDomain


ScheduleTimelineState = Literal["pending", "completed", "skipped", "recorded"]
ScheduleExecutionType = Literal["feeding", "pumping", "growth"]


class ScheduleTimelinePlanSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID = Field(description="当前用户计划的稳定 UUID。")
    domain: ScheduleDomain = Field(description="计划所属领域：泌乳、孕期、产后康复或通用。")
    plan_type: str = Field(description="计划在业务数据库中的具体类型。")
    title: str = Field(description="计划标题。")
    summary: str = Field(description="计划的简要目标与策略说明；没有摘要时为空字符串。")
    status: str = Field(description="计划持久化状态，当前列表只返回 active。")
    direction: str | None = Field(
        default=None,
        description="奶量计划方向，例如 increase、maintain 或 decrease；其他计划或未记录时为 null。",
    )
    start_date: date | None = Field(default=None, description="计划开始的本地日期；未记录时为 null。")
    end_date: date | None = Field(
        default=None,
        description="根据计划开始日期和覆盖天数计算的结束日期；信息不足时为 null。",
    )


class ScheduleTimelineSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID = Field(description="日程任务的稳定 UUID，可用于后续更新、状态变更或删除。")
    plan_id: UUID | None = Field(default=None, description="所属计划的稳定 UUID；独立任务时为 null。")
    scheduled_at: datetime | None = Field(
        default=None,
        description="按用户时区组合任务日期和时间得到的计划执行时间；信息不完整时为 null。",
    )
    title: str = Field(description="日程任务标题。")
    description: str = Field(description="日程任务说明；没有说明时为空字符串。")
    status: str = Field(description="任务持久化状态，例如 pending、completed 或 skipped。")
    completed_at: datetime | None = Field(
        default=None,
        description="任务被标记完成的系统时间；它不等同于业务事实的实际发生时间。",
    )


class ScheduleTimelineExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_type: ScheduleExecutionType = Field(description="实际执行事实类型：喂养、吸奶或宝宝生长测量。")
    record_id: UUID = Field(description="实际记录的稳定 UUID，可用于领域记录工具后续更正或删除。")
    plan_task_id: UUID | None = Field(
        default=None,
        description="该实际记录关联的日程任务 UUID；临时发生或未关联时为 null。",
    )
    infant_id: UUID | None = Field(
        default=None,
        description="记录对应的宝宝 UUID；妈妈侧吸奶记录或未指定宝宝时为 null。",
    )
    occurred_at: datetime = Field(description="实际执行发生时间，按用户时区返回。")
    ended_at: datetime | None = Field(default=None, description="实际执行结束时间；未记录时为 null。")
    title: str = Field(description="实际记录标题；没有标题时为空字符串。")
    volume_ml: float | None = Field(
        default=None,
        description="宝宝侧喂养摄入量，单位 ml；未记录或非喂养记录时为 null。",
    )
    milk_volume_ml: float | None = Field(
        default=None,
        description="妈妈侧吸奶产出量，单位 ml；未记录或非吸奶记录时为 null。",
    )
    duration_seconds: int | None = Field(
        default=None,
        description="喂养或吸奶持续时间，单位秒；未记录或生长测量时为 null。",
    )
    feed_type: str | None = Field(default=None, description="喂养方式；非喂养记录时为 null。")
    feed_action: str | None = Field(default=None, description="喂养动作；非喂养记录时为 null。")
    pump_type: str | None = Field(default=None, description="吸奶方式或设备类型；非吸奶记录时为 null。")
    source: str | None = Field(default=None, description="吸奶记录来源；非吸奶记录时为 null。")
    height_cm: float | None = Field(default=None, description="宝宝身高，单位 cm；未测量时为 null。")
    weight_kg: float | None = Field(default=None, description="宝宝体重，单位 kg；未测量时为 null。")
    head_cm: float | None = Field(default=None, description="宝宝头围，单位 cm；未测量时为 null。")


class ScheduleTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(
        description=(
            "时间线项目聚合标识，仅用于列表展示与去重；修改日程使用 schedule.task_id，"
            "修改实际记录使用 executions[].record_id。"
        )
    )
    domain: ScheduleDomain = Field(description="项目所属领域。")
    event_type: str = Field(description="领域内事件类型，例如 pumping、feeding、appointment 或 exercise。")
    state: ScheduleTimelineState = Field(
        description="归一化状态：待执行、已完成但无实际记录、已跳过，或已有实际执行事实。",
    )
    schedule: ScheduleTimelineSchedule | None = Field(
        default=None,
        description="计划日程；临时发生且没有关联日程的实际事实为 null。",
    )
    executions: list[ScheduleTimelineExecution] = Field(
        default_factory=list,
        description="与该日程关联的实际业务事实；未执行或仅手动完成的任务为空。",
    )


class ScheduleTimelineCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0, description="当前返回项目中仍待执行的计划数量。")
    completed: int = Field(ge=0, description="当前返回项目中已完成但没有实际记录的计划数量。")
    skipped: int = Field(ge=0, description="当前返回项目中已跳过的计划数量。")
    recorded: int = Field(ge=0, description="当前返回项目中包含实际执行事实的项目数量。")


class ScheduleTimelineReadOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date = Field(description="本次读取使用的可信运行时本地日期。")
    timezone: str = Field(description="计划时间和实际时间使用的 IANA 时区。")
    start_date: date = Field(description="时间线起始本地日期，包含该日。")
    end_date: date = Field(description="时间线结束本地日期，包含该日。")
    domains: list[ScheduleDomain] = Field(description="本次实际读取的日程领域。")
    plans: list[ScheduleTimelinePlanSummary] = Field(description="所选领域当前 active 计划的精简摘要。")
    items: list[ScheduleTimelineItem] = Field(description="按实际发生时间或计划执行时间升序排列的时间线项目。")
    counts: ScheduleTimelineCounts = Field(description="对当前返回项目按归一化状态汇总的数量。")
    truncated: bool = Field(description="是否因返回数量上限而省略了其他匹配项目。")


class ScheduleTimelineMutateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="本次日程变更结果状态，例如已执行、等待确认、无变化或失败。")
    entry_type: Literal["schedule", "execution"] = Field(
        description="本次变更的时间线对象类型：计划日程或实际执行记录。",
    )
    operation: Literal["create", "update", "delete", "set_status", "reschedule"] = Field(
        description="本次执行的通用日程操作。",
    )
    domain: ScheduleDomain = Field(description="后端根据计划、任务或实际记录确认的真实日程领域。")
    record_type: ScheduleExecutionType | None = Field(
        default=None,
        description="实际执行记录类型；计划日程变更且未生成记录时为 null。",
    )
    action_id: UUID | None = Field(default=None, description="本次业务变更对应的 Action UUID；无变化时为 null。")
    action_type: str | None = Field(default=None, description="后端实际执行的内部 Action 类型；无变化时为 null。")
    action_status: str | None = Field(default=None, description="Action 当前状态；无变化时为 null。")
    requires_confirmation: bool = Field(description="该变更是否仍需用户结构化确认。")
    confirmation_policy: Literal["always", "explicit_intent"] = Field(description="本次变更采用的确认策略。")
    user_visible: bool = Field(description="是否需要向 App 展示结构化确认卡。")
    write_succeeded: bool = Field(description="业务写入是否已经成功提交。")
    preview_payload: dict[str, Any] = Field(description="用于确认或结果说明的安全变更摘要。")
    error_code: str | None = Field(default=None, description="Action 失败时的稳定错误码；未失败时为 null。")
    artifact_id: UUID | None = Field(default=None, description="批量重排预览卡片 UUID；其他操作时为 null。")
    artifact_type: str | None = Field(default=None, description="批量重排预览卡片类型；其他操作时为 null。")
    plan_id: UUID | None = Field(default=None, description="批量重排涉及的计划 UUID；其他操作时为 null。")
    conflict_count: int | None = Field(default=None, ge=0, description="批量重排检测到的冲突数量。")
    updated_count: int | None = Field(default=None, ge=0, description="批量重排将调整的任务数量。")
    affected_dates: list[date] | None = Field(default=None, description="批量重排影响的本地日期列表。")
