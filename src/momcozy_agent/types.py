from __future__ import annotations

from typing import Any, Callable, Literal, NotRequired, Protocol, TypedDict

SkillId = Literal[
    "birth-prep",
    "milk-management",
    "emotion-support",
    "device-guidance",
]

AgentEventPhase = Literal[
    "started",
    "requesting_model",
    "model_tool_call",
    "loading_skill",
    "reading_skill_file",
    "executing_tool",
    "executing_script",
    "tool_completed",
    "completed",
    "failed",
]

ToolName = Literal[
    "list_skills",
    "load_skill",
    "search_skill_assets",
    "read_skill_file",
    "run_approved_skill_script",
    "ui_form_create",
    "ui_quick_replies_create",
    "birth_plan_form_create",
    "labor_communication_card_create",
    "birth_journey_plan_card_create",
    "birth_journey_plan_delete",
    "pregnancy_diary_manage",
    "hospital_bag_form_create",
    "hospital_bag_card_create",
    "hospital_bag_cart_update",
    "hospital_bag_pump_recommend",
    "ibclc_consult_card_create",
    "profile_get",
    "handoff_summary_generate",
    "device_manual_search",
    "support_ticket_draft_create",
    "milk_snapshot_get",
    "milk_status_query",
    "milk_records_query",
    "milk_record_mutate",
    "milk_plan_query",
    "milk_plan_mutate",
    "milk_calendar_query",
    "milk_calendar_change_preview",
    "milk_calendar_reschedule_preview",
    "milk_calendar_mutate",
    "milk_task_complete",
    "milk_assessment_evaluate",
    "milk_clinical_assessment_evaluate",
    "infant_growth_evaluate",
    "infant_growth_mutate",
    "milk_plan_preview",
]


class UserProfileSummary(TypedDict, total=False):
    user_id: str
    role: str
    pregnancy_status: str
    due_date: str | None
    postpartum_start_date: str | None
    region: str | None
    language: str
    unit_preference: str
    consent_flags: dict[str, bool]


class BabyProfileSummary(TypedDict, total=False):
    baby_id: str
    birth_date: str | None
    age_weeks: int | None
    feeding_type: str
    weight_history_summary: str | None
    known_conditions: list[str]


class ServiceStateSummary(TypedDict, total=False):
    active_birth_plan_id: str | None
    active_reminder_ids: list[str]
    open_case_ids: list[str]
    lactation_goal: str | None
    ibclc_case_id: str | None


class InputImage(TypedDict, total=False):
    image_url: str
    detail: Literal["auto", "low", "high"]
    mime_type: str
    name: str
    size: int


class RuntimeInputs(TypedDict):
    user_message: str
    user_id: NotRequired[str]
    locale: str
    timezone: NotRequired[str]
    message_sent_at: NotRequired[str]
    current_date: NotRequired[str]
    previous_response_id: NotRequired[str]
    user_profile: NotRequired[UserProfileSummary]
    baby_profile: NotRequired[BabyProfileSummary]
    service_state: NotRequired[ServiceStateSummary]
    retrieved_records: NotRequired[list[Any]]
    retrieved_knowledge: NotRequired[list[Any]]
    hospital_bag_cart: NotRequired[dict[str, Any]]
    images: NotRequired[list[InputImage]]


class AgentEvent(TypedDict, total=False):
    type: Literal["agent.status"]
    phase: AgentEventPhase
    message: str
    metadata: dict[str, Any]


AgentEventHandler = Callable[[AgentEvent], None]
TextDeltaHandler = Callable[[str], None]


class AgUiEvent(TypedDict, total=False):
    type: str
    timestamp: int
    thread_id: str
    run_id: str
    parent_run_id: str | None
    response_id: str
    output_index: int
    input: Any
    result: Any
    message: str
    code: str
    step_name: str
    message_id: str
    activity_type: str
    content: Any
    replace: bool
    tool_call_id: str
    tool_call_name: str
    item_id: str
    parent_message_id: str
    delta: str
    role: str
    name: str
    value: Any
    artifact_id: str
    artifact_type: str
    artifact: Any
    confirmation_id: str
    title: str
    status: str
    submit_label: str
    replies: Any


AgUiEventHandler = Callable[[AgUiEvent], None]


class SkillDefinition(TypedDict):
    id: SkillId
    name: str
    description: str


class FunctionToolDefinition(TypedDict):
    type: Literal["function"]
    name: ToolName
    description: str
    strict: bool
    parameters: dict[str, Any]
    defer_loading: NotRequired[bool]


class NamespaceToolDefinition(TypedDict):
    type: Literal["namespace"]
    name: str
    description: str
    tools: list[FunctionToolDefinition]


class ToolSearchDefinition(TypedDict, total=False):
    type: Literal["tool_search"]
    execution: Literal["server", "client"]


class WebSearchToolDefinition(TypedDict, total=False):
    type: Literal["web_search"]
    filters: dict[str, Any]
    external_web_access: bool


ToolDefinition = FunctionToolDefinition | NamespaceToolDefinition | ToolSearchDefinition | WebSearchToolDefinition


class ResponsesRequest(TypedDict, total=False):
    model: str
    instructions: str
    input: list[dict[str, Any]]
    previous_response_id: str
    tools: list[ToolDefinition]
    tool_choice: Literal["auto"] | dict[str, Any]
    include: list[str]
    reasoning: dict[str, Literal["low", "medium", "high"]]
    text: dict[str, Any]
    store: bool
    prompt_cache_key: str
    max_output_tokens: int
    metadata: dict[str, str]


class BuildAgentRequestOptions(TypedDict, total=False):
    model: str
    store: bool
    prompt_cache_key: str
    loaded_skill_ids: list[SkillId]
    context_state: Any
    enable_tools: bool
    max_output_tokens: int


class ResponsesClientLike(Protocol):
    class ResponsesLike(Protocol):
        def create(self, **request: Any) -> Any: ...

    responses: ResponsesLike
