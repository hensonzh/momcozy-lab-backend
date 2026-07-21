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
        "profile.read": ProfileReadToolHandler(service=profile_service),
        "profile.update": ProfileUpdateToolHandler(runtime_service=agent_runtime_service),
        "business.context.read": BusinessContextReadToolHandler(
            records_service=records_service,
            plans_service=plans_service,
            devices_service=devices_service,
        ),
        "records.milk_summary.read": MilkSummaryReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        ),
        "records.milk_status.read": MilkStatusReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        ),
        "records.milk_analysis.read": MilkAnalysisReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        ),
        "records.milk_analysis.intake": MilkAnalysisIntakeToolHandler(
            records_service=records_service,
            profile_service=profile_service,
            runtime_service=agent_runtime_service,
        ),
        "records.milk_analysis.evaluate": MilkAnalysisEvaluateToolHandler(runtime_service=agent_runtime_service),
        "records.growth.read": GrowthRecordsReadToolHandler(records_service=records_service),
        "plans.current.read": PlansCurrentReadToolHandler(plans_service=plans_service),
        "plans.calendar.read": PlansCalendarReadToolHandler(plans_service=plans_service),
        "pregnancy_diary.query": PregnancyDiaryQueryToolHandler(diary_service=diary_service),
        "pregnancy_diary.save": PregnancyDiarySaveToolHandler(runtime_service=agent_runtime_service),
        "pregnancy_diary.delete": PregnancyDiaryDeleteToolHandler(runtime_service=agent_runtime_service),
        "pregnancy.plan_context.read": PregnancyPlanContextReadToolHandler(
            profile_service=profile_service,
            plans_service=plans_service,
        ),
        "devices.pump_status.read": DevicesPumpStatusReadToolHandler(devices_service=devices_service),
        "devices.guidance.read": DeviceGuidanceReadToolHandler(
            asset_service=asset_service,
            reference_service=guidance_reference_service,
        ),
        "devices.unboxing.advance": DeviceUnboxingAdvanceToolHandler(
            runtime_service=agent_runtime_service,
            asset_service=asset_service,
            reference_service=guidance_reference_service,
        ),
        "conversation_history.image.load": ConversationHistoryImageLoadToolHandler(
            asset_service=asset_service,
            object_storage=object_storage,
        ),
        "plans.milk_plan.propose": MilkPlanProposeToolHandler(
            runtime_service=agent_runtime_service,
            plans_service=plans_service,
        ),
        "plans.milk_schedule.propose": MilkScheduleRescheduleProposeToolHandler(
            runtime_service=agent_runtime_service,
            plans_service=plans_service,
        ),
        "pregnancy.plan_intake.start": PregnancyPlanIntakeStartToolHandler(runtime_service=agent_runtime_service),
        "pregnancy.plan_intake.analyze": PregnancyPlanIntakeAnalyzeToolHandler(runtime_service=agent_runtime_service),
        "pregnancy.plan_intake.advance": PregnancyPlanIntakeAdvanceToolHandler(runtime_service=agent_runtime_service),
        "pregnancy.plan.propose": PregnancyPlanProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.task_create.propose": PlanTaskCreateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.task_complete.propose": PlanTaskCompleteProposeToolHandler(runtime_service=agent_runtime_service),
        "pregnancy.plan_todo.propose": PregnancyPlanTodoUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.task_update.propose": PlanTaskUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.task_delete.propose": PlanTaskDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.milk_task_update.propose": PlanTaskUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.milk_task_delete.propose": PlanTaskDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.plan_delete.propose": PlanDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "notifications.milk_reminder.propose": MilkReminderProposeToolHandler(runtime_service=agent_runtime_service),
        "records.feeding_record.propose": FeedingRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records.pumping_record.propose": PumpingRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records.feeding_record_delete.propose": FeedingRecordDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "records.pumping_record_delete.propose": PumpingRecordDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "records.growth_record.propose": GrowthRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records.growth_record_update.propose": GrowthRecordUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "records.growth_record_delete.propose": GrowthRecordDeleteProposeToolHandler(runtime_service=agent_runtime_service),
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
        "support.ticket.propose": SupportTicketProposeToolHandler(runtime_service=agent_runtime_service),
    }
    return handlers
