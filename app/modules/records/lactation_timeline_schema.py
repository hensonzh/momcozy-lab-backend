from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


LactationTimelineEventType = Literal["feeding", "pumping", "growth", "other"]
LactationTimelineState = Literal["pending", "completed", "skipped", "recorded"]
LactationTimelineRecordType = Literal["feeding", "pumping", "growth"]


class LactationTimelineSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID = Field(description="计划任务的稳定 UUID，可用于后续更新、跳过或删除该日程。")
    plan_id: UUID | None = Field(default=None, description="所属奶量计划的稳定 UUID；独立任务时为 null。")
    scheduled_at: datetime | None = Field(
        default=None,
        description="按用户时区组合任务日期和时间得到的计划执行时间；任务时间不完整时为 null。",
    )
    title: str = Field(description="计划任务标题。")
    description: str = Field(description="计划任务说明；没有说明时为空字符串。")
    status: str = Field(description="计划任务持久化状态，例如 pending、completed 或 skipped。")
    completed_at: datetime | None = Field(
        default=None,
        description="任务状态被标记为完成的系统时间；它不等同于喂养或吸奶的实际发生时间。",
    )


class LactationTimelineRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_type: LactationTimelineRecordType = Field(description="实际事实类型：喂养、吸奶或宝宝生长测量。")
    record_id: UUID = Field(description="实际记录的稳定 UUID，可用于后续更正或删除。")
    plan_task_id: UUID | None = Field(
        default=None,
        description="该实际记录完成的计划任务 UUID；临时记录或未关联记录时为 null。",
    )
    infant_id: UUID | None = Field(
        default=None,
        description="该记录对应的宝宝 UUID；妈妈侧吸奶记录或未指定宝宝时为 null。",
    )
    occurred_at: datetime = Field(description="喂养、吸奶或测量实际发生的时间，按用户时区返回。")
    ended_at: datetime | None = Field(
        default=None,
        description="实际事件结束时间；只有记录了吸奶结束时间时返回。",
    )
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
        description="本次喂养或吸奶持续时间，单位秒；未记录或生长测量时为 null。",
    )
    feed_type: str | None = Field(
        default=None,
        description="喂养方式；非喂养记录时为 null。",
    )
    feed_action: str | None = Field(
        default=None,
        description="喂养动作；非喂养记录时为 null。",
    )
    pump_type: str | None = Field(
        default=None,
        description="吸奶方式或设备类型；非吸奶记录时为 null。",
    )
    source: str | None = Field(
        default=None,
        description="吸奶记录来源，例如 manual、agent 或 device；非吸奶记录时为 null。",
    )
    height_cm: float | None = Field(
        default=None,
        description="宝宝身高，单位 cm；未测量或非生长记录时为 null。",
    )
    weight_kg: float | None = Field(
        default=None,
        description="宝宝体重，单位 kg；未测量或非生长记录时为 null。",
    )
    head_cm: float | None = Field(
        default=None,
        description="宝宝头围，单位 cm；未测量或非生长记录时为 null。",
    )


class LactationTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(
        description="时间线项目稳定标识；有关联日程时以计划任务为主，否则以实际记录为主。",
    )
    event_type: LactationTimelineEventType = Field(description="时间线事件类型：喂养、吸奶、生长测量或其他泌乳日程。")
    state: LactationTimelineState = Field(
        description="归一化状态：待执行、已完成但无记录、已跳过，或已有实际记录。",
    )
    schedule: LactationTimelineSchedule | None = Field(
        default=None,
        description="计划日程信息；临时发生且没有关联日程的实际记录为 null。",
    )
    records: list[LactationTimelineRecord] = Field(
        default_factory=list,
        description="该时间线项目包含的实际事实；未执行、跳过或仅手动完成的计划可以为空。",
    )


class LactationTimelineCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0, description="当前返回项目中仍待执行的计划数量。")
    completed: int = Field(ge=0, description="当前返回项目中已完成但没有实际记录的计划数量。")
    skipped: int = Field(ge=0, description="当前返回项目中已跳过的计划数量。")
    recorded: int = Field(ge=0, description="当前返回项目中包含一个或多个实际记录的项目数量。")


class LactationTimelineReadOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date = Field(description="本次读取使用的可信运行时本地日期。")
    timezone: str = Field(description="计划时间和实际时间使用的 IANA 时区。")
    start_date: date = Field(description="本次时间线读取的起始本地日期，包含该日。")
    end_date: date = Field(description="本次时间线读取的结束本地日期，包含该日。")
    items: list[LactationTimelineItem] = Field(description="按实际发生时间或计划执行时间升序排列的时间线项目。")
    counts: LactationTimelineCounts = Field(description="对当前返回项目按归一化状态汇总的数量。")
    truncated: bool = Field(description="是否因返回数量上限而省略了时间范围内的其他项目。")
