from __future__ import annotations


from app.infrastructure.object_storage.base import ObjectStorage
from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.executor import ToolHandler
from app.modules.assets.service import ProductAssetService
from app.modules.diary.service import DiaryService
from app.modules.plans.service import PlansService
from app.modules.plans.schedule_timeline import ScheduleTimelineService
from app.modules.profiles.lactation_context import LactationContextService
from app.modules.profiles.service import ProfileService
from app.modules.records.service import RecordsService
from app.agents.cozymate.device_guidance import DeviceGuidanceReferenceService
from app.agents.cozymate.tools.pump_models import PumpModelsReferenceService


from .birth_support import (
    SupportTicketProposeToolHandler,
    HospitalBagCartUpdateProposeToolHandler,
    IbclcConsultCardCreateToolHandler,
    HospitalBagWorkflowToolHandler,
)
from .milk import (
    MilkAnalysisToolHandler,
    MilkStatusReadToolHandler,
    MilkAnalysisReadToolHandler,
    MilkAnalysisIntakeToolHandler,
    MilkAnalysisEvaluateToolHandler,
    MaternalInfantProfileReadToolHandler,
    MaternalInfantProfileUpdateToolHandler,
)
from .plans_diary import (
    DiaryQueryToolHandler,
    DiaryMutateToolHandler,
)
from .plans import PlanMutateToolHandler, PlanReadToolHandler
from .schedule import ScheduleTimelineReadToolHandler, ScheduleTimelineMutateToolHandler
from .pregnancy_plan import PregnancyIntakeWorkflowToolHandler
from .devices import (
    DeviceGuidanceToolHandler,
    PumpModelsReadToolHandler,
    ConversationHistoryImageLoadToolHandler,
)


def build_default_tool_handlers(
    *,
    profile_service: ProfileService,
    lactation_context_service: LactationContextService,
    records_service: RecordsService,
    plans_service: PlansService,
    diary_service: DiaryService,
    asset_service: ProductAssetService,
    agent_runtime_service: AgentRuntimeService,
    object_storage: ObjectStorage | None = None,
    device_guidance_reference_service: DeviceGuidanceReferenceService | None = None,
) -> dict[str, ToolHandler]:
    guidance_reference_service = device_guidance_reference_service or DeviceGuidanceReferenceService()
    pump_models_service = PumpModelsReferenceService(object_storage=object_storage)
    handlers: dict[str, ToolHandler] = {
        "profile_read": MaternalInfantProfileReadToolHandler(
            service=lactation_context_service
        ),
        "profile_update": MaternalInfantProfileUpdateToolHandler(
            runtime_service=agent_runtime_service,
            lactation_context_service=lactation_context_service,
        ),
        "schedule_timeline_mutate": ScheduleTimelineMutateToolHandler(
            runtime_service=agent_runtime_service,
            plans_service=plans_service,
        ),
        "schedule_timeline_read": ScheduleTimelineReadToolHandler(
            service=ScheduleTimelineService(
                records_service=records_service,
                plans_service=plans_service,
            )
        ),
        "milk_analysis_manage": MilkAnalysisToolHandler(
            summary_handler=MilkStatusReadToolHandler(
                records_service=records_service,
                profile_service=profile_service,
            ),
            detailed_handler=MilkAnalysisReadToolHandler(
                records_service=records_service,
                profile_service=profile_service,
            ),
            intake_handler=MilkAnalysisIntakeToolHandler(
                records_service=records_service,
                profile_service=profile_service,
                runtime_service=agent_runtime_service,
            ),
            evaluate_handler=MilkAnalysisEvaluateToolHandler(runtime_service=agent_runtime_service),
        ),
        "diary_read": DiaryQueryToolHandler(diary_service=diary_service),
        "diary_mutate": DiaryMutateToolHandler(runtime_service=agent_runtime_service),
        "devices_guidance_manage": DeviceGuidanceToolHandler(
            runtime_service=agent_runtime_service,
            asset_service=asset_service,
            reference_service=guidance_reference_service,
        ),
        "pump_models_read": PumpModelsReadToolHandler(service=pump_models_service),
        "conversation_history_image_read": ConversationHistoryImageLoadToolHandler(
            asset_service=asset_service,
            object_storage=object_storage,
        ),
        "plan_read": PlanReadToolHandler(
            plans_service=plans_service,
        ),
        "plan_mutate": PlanMutateToolHandler(
            runtime_service=agent_runtime_service,
            plans_service=plans_service,
        ),
        "pregnancy_intake_manage": PregnancyIntakeWorkflowToolHandler(
            runtime_service=agent_runtime_service,
        ),
        "hospital_bag_manage": HospitalBagWorkflowToolHandler(
            runtime_service=agent_runtime_service
        ),
        "hospital_bag_cart_mutate": HospitalBagCartUpdateProposeToolHandler(
            runtime_service=agent_runtime_service,
            pump_models_service=pump_models_service,
        ),
        "ibclc_consult_card_create": IbclcConsultCardCreateToolHandler(runtime_service=agent_runtime_service),
        "support_ticket_draft_create": SupportTicketProposeToolHandler(runtime_service=agent_runtime_service),
    }
    return handlers
