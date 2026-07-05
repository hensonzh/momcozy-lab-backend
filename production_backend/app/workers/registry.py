from __future__ import annotations

from collections.abc import Mapping

from .outbox import OutboxHandler
from ..infrastructure.object_storage import ObjectStorage
from ..modules.agent_runtime.actions.outbox import AgentActionApplyHandler, AgentActionOutboxHandler
from ..modules.agent_runtime.event_stream.sink import AgentEventSink
from ..modules.agent_runtime.repository import AgentRuntimeRepository
from ..modules.agent_runtime.service import AGENT_ACTION_APPLY_JOB
from ..modules.files.outbox_handlers import FileObjectDeleteHandler
from ..modules.files.service import FILE_OBJECT_DELETE_JOB
from ..modules.hospital_bag import HOSPITAL_BAG_CART_UPDATE_ACTION, HospitalBagCartUpdateActionHandler


def build_outbox_handlers(
    *,
    object_storage: ObjectStorage,
    agent_runtime_repository: AgentRuntimeRepository | None = None,
    agent_event_sink: AgentEventSink | None = None,
    agent_action_handlers: Mapping[str, AgentActionApplyHandler] | None = None,
) -> dict[str, OutboxHandler]:
    handlers: dict[str, OutboxHandler] = {FILE_OBJECT_DELETE_JOB: FileObjectDeleteHandler(object_storage=object_storage)}
    if agent_runtime_repository is not None:
        resolved_agent_action_handlers = default_agent_action_handlers()
        if agent_action_handlers is not None:
            resolved_agent_action_handlers.update(agent_action_handlers)
        handlers[AGENT_ACTION_APPLY_JOB] = AgentActionOutboxHandler(
            repository=agent_runtime_repository,
            handlers=resolved_agent_action_handlers,
            event_sink=agent_event_sink,
        )
    return handlers


def default_agent_action_handlers() -> dict[str, AgentActionApplyHandler]:
    return {HOSPITAL_BAG_CART_UPDATE_ACTION: HospitalBagCartUpdateActionHandler()}
