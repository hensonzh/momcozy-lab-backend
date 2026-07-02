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
