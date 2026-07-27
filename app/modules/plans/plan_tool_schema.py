from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlanToolItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID = Field(description="当前用户计划的稳定 UUID。")
    plan_type: str = Field(description="计划的真实业务类型，由后端持久化数据确定。")
    title: str = Field(description="计划标题。")
    summary: str = Field(description="计划摘要；没有摘要时为空字符串。")
    status: str = Field(description="计划持久化状态，例如 active。")
    source: str = Field(description="计划来源，例如 agent_action 或 manual。")
    version: int = Field(ge=1, description="计划当前版本；更新时作为 expected_version 使用。")
    starts_on: date | None = Field(default=None, description="计划生效日期；未设置时为 null。")
    ends_on: date | None = Field(
        default=None,
        description="计划最后一个有效日期；无固定结束日期时为 null。",
    )
    created_at: datetime | None = Field(default=None, description="计划创建时间；不可用时为 null。")
    updated_at: datetime | None = Field(default=None, description="计划最近更新时间；不可用时为 null。")
    content: dict[str, Any] = Field(
        default_factory=dict,
        description="按计划类型投影的安全计划内容；列表模式或没有可返回内容时为空对象。",
    )


class PlanReadOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["plans_read", "plan_read"] = Field(description="本次读取结果状态。")
    mode: Literal["list", "detail"] = Field(description="本次读取模式：计划列表或单个计划详情。")
    plan: PlanToolItem | None = Field(description="detail 模式返回的计划；list 模式为 null。")
    plans: list[PlanToolItem] = Field(description="list 模式返回的计划；detail 模式为空列表。")
    count: int = Field(ge=0, description="本次返回的计划数量。")
    truncated: bool = Field(description="是否可能还有因 limit 未返回的匹配计划。")


class PlanMutateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["applied", "confirmation_required", "input_required", "blocked", "failed"] = Field(
        description="统一结果状态：已写入、待确认、需补充输入、安全阻断或失败。"
    )
    result_code: str = Field(description="更具体的稳定结果码，例如 action_applied 或 needs_pregnancy_plan_intake。")
    operation: Literal["create", "update", "delete"] = Field(description="本次计划资源变更操作。")
    plan_type: str = Field(description="创建时选择或由已有计划确定的真实计划类型。")
    plan_id: UUID | None = Field(
        default=None,
        description="已持久化计划的稳定 UUID；尚未写入计划时为 null。",
    )
    plan_version: int | None = Field(
        default=None,
        ge=1,
        description="写入后的权威计划版本；尚未写入或该操作不返回版本时为 null。",
    )
    resource_type: str | None = Field(default=None, description="Action 实际写入的资源类型。")
    resource_id: str | None = Field(default=None, description="Action 实际写入的资源稳定标识。")
    result_details: dict[str, Any] | None = Field(
        default=None,
        description="Action handler 返回的权威写入详情，例如版本和任务数量。",
    )
    action_id: UUID | None = Field(default=None, description="本次业务变更对应的 Action UUID。")
    action_type: str | None = Field(default=None, description="后端实际执行的领域 Action 类型。")
    action_status: str | None = Field(default=None, description="Action 当前状态。")
    requires_confirmation: bool | None = Field(default=None, description="该 Action 是否仍需用户确认。")
    confirmation_policy: Literal["always", "explicit_intent"] | None = Field(
        default=None,
        description="本次 Action 使用的确认策略。",
    )
    user_visible: bool | None = Field(default=None, description="是否需要展示结构化确认界面。")
    write_succeeded: bool | None = Field(default=None, description="计划写入是否已经成功提交。")
    preview_payload: dict[str, Any] | None = Field(default=None, description="安全的计划变更预览摘要。")
    error_code: str | None = Field(default=None, description="Action 失败时的稳定错误码。")
    failure_message: str | None = Field(default=None, description="计划变更失败时供模型和用户理解结果的事实说明。")
    artifact_id: UUID | None = Field(default=None, description="计划预览卡片 UUID。")
    artifact_type: str | None = Field(default=None, description="计划预览卡片类型。")
    title: str | None = Field(default=None, description="生成的计划标题。")
    summary: str | None = Field(default=None, description="生成的计划摘要。")
    task_count: int | None = Field(default=None, ge=0, description="生成的计划日程任务数量。")
    next_step: str | None = Field(default=None, description="计划生成前置流程尚未完成时的下一步。")
    signal_ids: list[str] | None = Field(default=None, description="阻断计划创建的孕期安全信号标识。")
    blocks_plan_flow: bool | None = Field(default=None, description="安全信号是否阻断本次计划创建。")
    required_response: str | None = Field(default=None, description="安全阻断时必须向用户返回的指导。")
    agent_instruction: str | None = Field(
        default=None,
        description="安全阻断时供智能体执行的简洁响应约束；其他结果为 null。",
    )
