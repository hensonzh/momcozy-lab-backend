from __future__ import annotations

from app.infrastructure.object_storage.base import ObjectStorage
from app.agent_runtime.events.publisher import AgentEventPublisher
from app.agent_runtime.events.transient import AgentTransientStream
from app.agent_runtime.runs.repository import AgentRuntimeRepository
from app.agent_runtime.tools.executor import ToolExecutor, ToolHandler
from app.agent_runtime.tools.payloads import DEFAULT_MAX_INLINE_PAYLOAD_BYTES
from app.agent_runtime.tools.registry import ToolContractRegistry
from app.core.metrics import RequestMetrics

from .policy import CozymateToolExecutionPolicy


class CozymateToolExecutor(ToolExecutor):
    def __init__(
        self,
        *,
        registry: ToolContractRegistry,
        repository: AgentRuntimeRepository,
        handlers: dict[str, ToolHandler] | None = None,
        event_sink: AgentEventPublisher | None = None,
        metrics: RequestMetrics | None = None,
        object_storage: ObjectStorage | None = None,
        max_inline_output_bytes: int = DEFAULT_MAX_INLINE_PAYLOAD_BYTES,
        transient_stream: AgentTransientStream | None = None,
    ) -> None:
        super().__init__(
            registry=registry,
            repository=repository,
            handlers=handlers,
            event_sink=event_sink,
            metrics=metrics,
            object_storage=object_storage,
            max_inline_output_bytes=max_inline_output_bytes,
            transient_stream=transient_stream,
            policy=CozymateToolExecutionPolicy(),
        )
