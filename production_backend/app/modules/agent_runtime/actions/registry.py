from __future__ import annotations

from typing import Any

from ...hospital_bag.agent_actions import HOSPITAL_BAG_CART_UPDATE_ACTION, HospitalBagCartUpdateActionHandler
from ...notifications.agent_actions import MILK_REMINDER_CREATE_ACTION, MilkReminderCreateActionHandler
from ...plans.agent_actions import (
    MILK_PLAN_CREATE_ACTION,
    MILK_SCHEDULE_RESCHEDULE_ACTION,
    PLAN_DELETE_ACTION,
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
    PREGNANCY_PLAN_CREATE_ACTION,
    PREGNANCY_PLAN_TODO_UPDATE_ACTION,
    MilkPlanCreateActionHandler,
    MilkScheduleRescheduleActionHandler,
    PlanDeleteActionHandler,
    PlanTaskCompleteActionHandler,
    PlanTaskCreateActionHandler,
    PlanTaskDeleteActionHandler,
    PlanTaskUpdateActionHandler,
    PregnancyPlanCreateActionHandler,
    PregnancyPlanTodoUpdateActionHandler,
)
from ...records.agent_actions import (
    FEEDING_RECORD_CREATE_ACTION,
    FEEDING_RECORD_DELETE_ACTION,
    GROWTH_RECORD_CREATE_ACTION,
    GROWTH_RECORD_DELETE_ACTION,
    GROWTH_RECORD_UPDATE_ACTION,
    PUMPING_RECORD_CREATE_ACTION,
    PUMPING_RECORD_DELETE_ACTION,
    FeedingRecordCreateActionHandler,
    FeedingRecordDeleteActionHandler,
    GrowthRecordCreateActionHandler,
    GrowthRecordDeleteActionHandler,
    GrowthRecordUpdateActionHandler,
    PumpingRecordCreateActionHandler,
    PumpingRecordDeleteActionHandler,
)
from ...support.agent_actions import SUPPORT_TICKET_CREATE_ACTION, SupportTicketCreateActionHandler
from .executor import AgentActionApplyHandler


def build_agent_action_handlers(
    *,
    notifications_service: Any,
    plans_service: Any,
    records_service: Any,
    support_service: Any,
) -> dict[str, AgentActionApplyHandler]:
    return {
        HOSPITAL_BAG_CART_UPDATE_ACTION: HospitalBagCartUpdateActionHandler(),
        MILK_REMINDER_CREATE_ACTION: MilkReminderCreateActionHandler(service=notifications_service),
        MILK_PLAN_CREATE_ACTION: MilkPlanCreateActionHandler(service=plans_service),
        MILK_SCHEDULE_RESCHEDULE_ACTION: MilkScheduleRescheduleActionHandler(service=plans_service),
        PREGNANCY_PLAN_CREATE_ACTION: PregnancyPlanCreateActionHandler(service=plans_service),
        PREGNANCY_PLAN_TODO_UPDATE_ACTION: PregnancyPlanTodoUpdateActionHandler(service=plans_service),
        PLAN_TASK_CREATE_ACTION: PlanTaskCreateActionHandler(service=plans_service),
        PLAN_TASK_COMPLETE_ACTION: PlanTaskCompleteActionHandler(service=plans_service),
        PLAN_TASK_UPDATE_ACTION: PlanTaskUpdateActionHandler(service=plans_service),
        PLAN_TASK_DELETE_ACTION: PlanTaskDeleteActionHandler(service=plans_service),
        PLAN_DELETE_ACTION: PlanDeleteActionHandler(service=plans_service),
        FEEDING_RECORD_CREATE_ACTION: FeedingRecordCreateActionHandler(service=records_service),
        PUMPING_RECORD_CREATE_ACTION: PumpingRecordCreateActionHandler(service=records_service),
        FEEDING_RECORD_DELETE_ACTION: FeedingRecordDeleteActionHandler(service=records_service),
        PUMPING_RECORD_DELETE_ACTION: PumpingRecordDeleteActionHandler(service=records_service),
        GROWTH_RECORD_CREATE_ACTION: GrowthRecordCreateActionHandler(service=records_service),
        GROWTH_RECORD_UPDATE_ACTION: GrowthRecordUpdateActionHandler(service=records_service),
        GROWTH_RECORD_DELETE_ACTION: GrowthRecordDeleteActionHandler(service=records_service),
        SUPPORT_TICKET_CREATE_ACTION: SupportTicketCreateActionHandler(service=support_service),
    }
