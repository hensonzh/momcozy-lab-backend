from __future__ import annotations


from app.infrastructure.object_storage.base import ObjectStorage
from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.executor import ToolHandler
from app.modules.assets.service import ProductAssetService
from app.modules.devices.service import DevicesService
from app.modules.diary.service import DiaryService
from app.modules.plans.service import PlansService
from app.modules.profiles.service import ProfileService
from app.modules.records.service import RecordsService
from app.agents.cozymate.device_guidance import DeviceGuidanceReferenceService


from .birth_support import (
    ProfileReadToolHandler,
    ProfileUpdateToolHandler,
    SupportTicketProposeToolHandler,
    HospitalBagCartUpdateProposeToolHandler,
    IbclcConsultCardCreateToolHandler,
    BirthPreparationArtifactToolHandler,
    HospitalBagFormCreateToolHandler,
    HospitalBagCardCreateToolHandler,
    PregnancyPlanIntakeStartToolHandler,
    PregnancyPlanIntakeAnalyzeToolHandler,
    PregnancyPlanIntakeAdvanceToolHandler,
)
from .milk import (
    BusinessContextReadToolHandler,
    MilkSummaryReadToolHandler,
    MilkStatusReadToolHandler,
    MilkAnalysisReadToolHandler,
    MilkAnalysisIntakeToolHandler,
    MilkAnalysisEvaluateToolHandler,
    GrowthRecordsReadToolHandler,
    FeedingRecordProposeToolHandler,
    PumpingRecordProposeToolHandler,
    FeedingRecordDeleteProposeToolHandler,
    PumpingRecordDeleteProposeToolHandler,
    GrowthRecordProposeToolHandler,
    GrowthRecordUpdateProposeToolHandler,
    GrowthRecordDeleteProposeToolHandler,
    MilkPlanProposeToolHandler,
    MilkScheduleRescheduleProposeToolHandler,
)
from .plans_diary import (
    PlansCurrentReadToolHandler,
    PlansCalendarReadToolHandler,
    PregnancyDiaryQueryToolHandler,
    PregnancyDiarySaveToolHandler,
    PregnancyDiaryDeleteToolHandler,
    PregnancyPlanContextReadToolHandler,
    PregnancyPlanProposeToolHandler,
    PlanTaskCreateProposeToolHandler,
    PlanTaskCompleteProposeToolHandler,
    PregnancyPlanTodoUpdateProposeToolHandler,
    PlanTaskUpdateProposeToolHandler,
    PlanTaskDeleteProposeToolHandler,
    PlanDeleteProposeToolHandler,
    MilkReminderProposeToolHandler,
)
from .devices import (
    DevicesPumpStatusReadToolHandler,
    DeviceGuidanceReadToolHandler,
    DeviceUnboxingAdvanceToolHandler,
    ConversationHistoryImageLoadToolHandler,
)


def build_default_tool_handlers(
    *,
    profile_service: ProfileService,
    records_service: RecordsService,
    plans_service: PlansService,
    diary_service: DiaryService,
    devices_service: DevicesService,
    asset_service: ProductAssetService,
    agent_runtime_service: AgentRuntimeService,
    object_storage: ObjectStorage | None = None,
    device_guidance_reference_service: DeviceGuidanceReferenceService | None = None,
) -> dict[str, ToolHandler]:
    guidance_reference_service = device_guidance_reference_service or DeviceGuidanceReferenceService()
    handlers: dict[str, ToolHandler] = {
        "profile_read": ProfileReadToolHandler(service=profile_service),
        "profile_update": ProfileUpdateToolHandler(runtime_service=agent_runtime_service),
        "business_context_read": BusinessContextReadToolHandler(
            records_service=records_service,
            plans_service=plans_service,
            devices_service=devices_service,
        ),
        "records_milk_summary_read": MilkSummaryReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        ),
        "records_milk_status_read": MilkStatusReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        ),
        "records_milk_analysis_read": MilkAnalysisReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        ),
        "records_milk_analysis_intake": MilkAnalysisIntakeToolHandler(
            records_service=records_service,
            profile_service=profile_service,
            runtime_service=agent_runtime_service,
        ),
        "records_milk_analysis_evaluate": MilkAnalysisEvaluateToolHandler(runtime_service=agent_runtime_service),
        "records_growth_read": GrowthRecordsReadToolHandler(records_service=records_service),
        "plans_current_read": PlansCurrentReadToolHandler(plans_service=plans_service),
        "plans_calendar_read": PlansCalendarReadToolHandler(plans_service=plans_service),
        "pregnancy_diary_query": PregnancyDiaryQueryToolHandler(diary_service=diary_service),
        "pregnancy_diary_save": PregnancyDiarySaveToolHandler(runtime_service=agent_runtime_service),
        "pregnancy_diary_delete": PregnancyDiaryDeleteToolHandler(runtime_service=agent_runtime_service),
        "pregnancy_plan_context_read": PregnancyPlanContextReadToolHandler(
            profile_service=profile_service,
            plans_service=plans_service,
        ),
        "devices_pump_status_read": DevicesPumpStatusReadToolHandler(devices_service=devices_service),
        "devices_guidance_read": DeviceGuidanceReadToolHandler(
            asset_service=asset_service,
            reference_service=guidance_reference_service,
        ),
        "devices_unboxing_advance": DeviceUnboxingAdvanceToolHandler(
            runtime_service=agent_runtime_service,
            asset_service=asset_service,
            reference_service=guidance_reference_service,
        ),
        "conversation_history_image_load": ConversationHistoryImageLoadToolHandler(
            asset_service=asset_service,
            object_storage=object_storage,
        ),
        "plans_milk_plan_propose": MilkPlanProposeToolHandler(
            runtime_service=agent_runtime_service,
            plans_service=plans_service,
        ),
        "plans_milk_schedule_propose": MilkScheduleRescheduleProposeToolHandler(
            runtime_service=agent_runtime_service,
            plans_service=plans_service,
        ),
        "pregnancy_plan_intake_start": PregnancyPlanIntakeStartToolHandler(runtime_service=agent_runtime_service),
        "pregnancy_plan_intake_analyze": PregnancyPlanIntakeAnalyzeToolHandler(runtime_service=agent_runtime_service),
        "pregnancy_plan_intake_advance": PregnancyPlanIntakeAdvanceToolHandler(runtime_service=agent_runtime_service),
        "pregnancy_plan_propose": PregnancyPlanProposeToolHandler(runtime_service=agent_runtime_service),
        "plans_task_create_propose": PlanTaskCreateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans_task_complete_propose": PlanTaskCompleteProposeToolHandler(runtime_service=agent_runtime_service),
        "pregnancy_plan_todo_propose": PregnancyPlanTodoUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans_task_update_propose": PlanTaskUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans_task_delete_propose": PlanTaskDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "plans_milk_task_update_propose": PlanTaskUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans_milk_task_delete_propose": PlanTaskDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "plans_plan_delete_propose": PlanDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "notifications_milk_reminder_propose": MilkReminderProposeToolHandler(runtime_service=agent_runtime_service),
        "records_feeding_record_propose": FeedingRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records_pumping_record_propose": PumpingRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records_feeding_record_delete_propose": FeedingRecordDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "records_pumping_record_delete_propose": PumpingRecordDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "records_growth_record_propose": GrowthRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records_growth_record_update_propose": GrowthRecordUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "records_growth_record_delete_propose": GrowthRecordDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "birth_plan_form_create": BirthPreparationArtifactToolHandler(runtime_service=agent_runtime_service, tool_name="birth_plan_form_create"),
        "labor_communication_card_create": BirthPreparationArtifactToolHandler(
            runtime_service=agent_runtime_service, tool_name="labor_communication_card_create"
        ),
        "hospital_bag_form_create": HospitalBagFormCreateToolHandler(runtime_service=agent_runtime_service),
        "hospital_bag_card_create": HospitalBagCardCreateToolHandler(runtime_service=agent_runtime_service),
        "hospital_bag_cart_update": HospitalBagCartUpdateProposeToolHandler(
            runtime_service=agent_runtime_service
        ),
        "hospital_bag_pump_recommend": BirthPreparationArtifactToolHandler(
            runtime_service=agent_runtime_service, tool_name="hospital_bag_pump_recommend"
        ),
        "ibclc_consult_card_create": IbclcConsultCardCreateToolHandler(runtime_service=agent_runtime_service),
        "support_ticket_propose": SupportTicketProposeToolHandler(runtime_service=agent_runtime_service),
    }
    return handlers
