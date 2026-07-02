from __future__ import annotations

from collections.abc import Mapping

from .outbox import OutboxHandler
from ..infrastructure.object_storage import ObjectStorage
from ..modules.agent_runtime.action_outbox import AgentActionApplyHandler, AgentActionOutboxHandler
from ..modules.agent_runtime.events import AgentEventSink
from ..modules.agent_runtime.repository import AgentRuntimeRepository
from ..modules.agent_runtime.service import AGENT_ACTION_APPLY_JOB
from ..modules.files.outbox_handlers import FileObjectDeleteHandler
from ..modules.files.service import FILE_OBJECT_DELETE_JOB


def build_outbox_handlers(
    *,
    object_storage: ObjectStorage,
    agent_runtime_repository: AgentRuntimeRepository | None = None,
    agent_event_sink: AgentEventSink | None = None,
    agent_action_handlers: Mapping[str, AgentActionApplyHandler] | None = None,
) -> dict[str, OutboxHandler]:
    handlers: dict[str, OutboxHandler] = {FILE_OBJECT_DELETE_JOB: FileObjectDeleteHandler(object_storage=object_storage)}
    if agent_runtime_repository is not None:
        handlers[AGENT_ACTION_APPLY_JOB] = AgentActionOutboxHandler(
            repository=agent_runtime_repository,
            handlers=agent_action_handlers or {},
            event_sink=agent_event_sink,
        )
    return handlers
