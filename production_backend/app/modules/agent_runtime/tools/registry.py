from __future__ import annotations

from ....core.errors import ApiError
from .contracts import ToolContract


class ToolContractRegistry:
    def __init__(self) -> None:
        self._contracts: dict[str, ToolContract] = {}

    def register(self, contract: ToolContract) -> None:
        if contract.name in self._contracts:
            raise ApiError(code="conflict", message="Tool contract is already registered.", status=409)
        self._contracts[contract.name] = contract

    def get(self, name: str) -> ToolContract:
        contract = self._contracts.get(name)
        if contract is None:
            raise ApiError(code="not_found", message="Tool contract not found.", status=404)
        return contract

    def list(self) -> list[ToolContract]:
        return list(self._contracts.values())

    def names_for_sdk(self) -> tuple[str, ...]:
        return tuple(sorted(self._contracts))


def default_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="profile.read",
            domain="profiles",
            description="Read current user's profile projection for context.",
            input_schema_ref="ProfileContextQuery",
            output_schema_ref="ProfileContextRead",
            read_or_write="read",
            required_permission="profile:read:self",
            owner_scope="actor",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="business.context.read",
            domain="business_context",
            description="Read current user's recent records, plans, diary, and device context as a bounded summary.",
            input_schema_ref="BusinessContextQuery",
            output_schema_ref="BusinessContextRead",
            read_or_write="read",
            required_permission="business_context:read:self",
            owner_scope="actor",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="records.milk_summary.read",
            domain="records",
            description="Read a bounded milk-management summary from recent feeding, pumping, and trend records.",
            input_schema_ref="MilkSummaryQuery",
            output_schema_ref="MilkSummaryRead",
            read_or_write="read",
            required_permission="business_context:read:self",
            owner_scope="actor",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="plans.current.read",
            domain="plans",
            description="Read current user's active plans and recent tasks as a bounded summary.",
            input_schema_ref="PlansCurrentQuery",
            output_schema_ref="PlansCurrentRead",
            read_or_write="read",
            required_permission="business_context:read:self",
            owner_scope="actor",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="diary.recent.read",
            domain="diary",
            description="Read current user's recent pregnancy diary entries as a bounded summary.",
            input_schema_ref="DiaryRecentQuery",
            output_schema_ref="DiaryRecentRead",
            read_or_write="read",
            required_permission="business_context:read:self",
            owner_scope="actor",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="diary.entry_upsert.propose",
            domain="diary",
            description="Propose a pregnancy diary entry upsert action for user confirmation.",
            input_schema_ref="DiaryEntryUpsertProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="diary:write:self",
            owner_scope="actor",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="devices.pump_status.read",
            domain="devices",
            description="Read current user's pump devices and recent telemetry as a bounded status summary.",
            input_schema_ref="DevicesPumpStatusQuery",
            output_schema_ref="DevicesPumpStatusRead",
            read_or_write="read",
            required_permission="business_context:read:self",
            owner_scope="actor",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="files.vision_summary.read",
            domain="files",
            description="Read an owner-scoped uploaded image and return a bounded vision summary.",
            input_schema_ref="FileVisionSummaryQuery",
            output_schema_ref="FileVisionSummaryRead",
            read_or_write="read",
            required_permission="files:read:self",
            owner_scope="actor",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=20,
        )
    )
    registry.register(
        ToolContract(
            name="plans.milk_plan.propose",
            domain="plans",
            description="Propose a milk-management plan create action for user confirmation.",
            input_schema_ref="MilkPlanProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="plans:write:self",
            owner_scope="actor",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="pregnancy.plan_create.propose",
            domain="plans",
            description="Propose a pregnancy plan create action for user confirmation.",
            input_schema_ref="PregnancyPlanProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="plans:write:self",
            owner_scope="actor",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="plans.task_create.propose",
            domain="plans",
            description="Propose a plan task create action for user confirmation.",
            input_schema_ref="PlanTaskCreateProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="plans:write:self",
            owner_scope="actor",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="plans.task_complete.propose",
            domain="plans",
            description="Propose a plan task completion action for user confirmation.",
            input_schema_ref="PlanTaskCompleteProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="plans:write:self",
            owner_scope="actor",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="notifications.milk_reminder.propose",
            domain="notifications",
            description="Propose a milk-management reminder notification for user confirmation.",
            input_schema_ref="MilkReminderProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="notifications:create:self",
            owner_scope="actor",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="records.feeding_record.propose",
            domain="records",
            description="Propose a feeding record create action for user confirmation.",
            input_schema_ref="FeedingRecordProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="records:write:self",
            owner_scope="actor",
            side_effect_level="low",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="records.pumping_record.propose",
            domain="records",
            description="Propose a pumping record create action for user confirmation.",
            input_schema_ref="PumpingRecordProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="records:write:self",
            owner_scope="actor",
            side_effect_level="low",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="hospital_bag.cart_update.propose",
            domain="hospital_bag",
            description="Propose a hospital-bag cart update for user confirmation.",
            input_schema_ref="HospitalBagCartUpdateProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="hospital_bag_cart:update:self",
            owner_scope="actor",
            side_effect_level="low",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="support.ticket.propose",
            domain="support",
            description="Create a support ticket action proposal after user confirmation intent is clear.",
            input_schema_ref="SupportTicketProposalCreate",
            output_schema_ref="AgentActionRead",
            read_or_write="write",
            required_permission="support_ticket:create:self",
            owner_scope="actor",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    return registry
