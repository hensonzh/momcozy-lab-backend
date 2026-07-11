from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from ..models import AgentEvent
from ..repository import AgentRuntimeRepository
from ..run_lifecycle.controls import AgentRunControls
from .transient import AgentTransientStream


LOGGER = logging.getLogger("production_backend.agent_runtime.events")


class AgentEventPublisher:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        controls: AgentRunControls | None = None,
        after_append: Callable[[], Awaitable[None]] | None = None,
        transient_stream: AgentTransientStream | None = None,
    ) -> None:
        self.repository = repository
        self.controls = controls
        self.after_append = after_append
        self.transient_stream = transient_stream

    async def append_event(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> AgentEvent:
        event = await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
        if self.controls is not None:
            await self.controls.set_stream_cursor(run_id=run_id, sequence=event.sequence)
        if self.after_append is not None:
            await self.after_append()
        return event

    async def publish_application_event(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        dedupe_key: str,
        optimistic: bool,
        durable: bool,
        ignore_errors: bool = True,
    ) -> None:
        if self.transient_stream is None:
            return
        normalized_dedupe_key = str(dedupe_key or "").strip()
        if not normalized_dedupe_key:
            return
        try:
            await self.transient_stream.publish_application_event(
                thread_id=thread_id,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                dedupe_key=normalized_dedupe_key,
                optimistic=optimistic,
                durable=durable,
            )
        except Exception:
            if not ignore_errors:
                raise
            LOGGER.warning("Failed to publish live agent event.", exc_info=True)

    async def publish_progress(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        phase: str,
        label: str,
        semantic: dict[str, Any] | None = None,
        dedupe_key: str = "",
        optimistic: bool = True,
        durable: bool = False,
        ignore_errors: bool = True,
    ) -> None:
        if self.transient_stream is None:
            return
        try:
            await self.transient_stream.publish_progress(
                thread_id=thread_id,
                run_id=run_id,
                phase=phase,
                label=label,
                semantic=semantic,
                dedupe_key=dedupe_key,
                optimistic=optimistic,
                durable=durable,
            )
        except Exception:
            if not ignore_errors:
                raise
            LOGGER.warning("Failed to publish live run.progress event.", exc_info=True)

    async def publish_message_delta(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        delta: str,
        message_stream_id: str = "assistant",
        segment_index: int | None = None,
        prefix_utf8_bytes: int | None = None,
        prefix_sha256: str = "",
        ignore_errors: bool = False,
    ) -> None:
        if self.transient_stream is None:
            return
        try:
            await self.transient_stream.publish_message_delta(
                thread_id=thread_id,
                run_id=run_id,
                delta=delta,
                message_stream_id=message_stream_id,
                segment_index=segment_index,
                prefix_utf8_bytes=prefix_utf8_bytes,
                prefix_sha256=prefix_sha256,
            )
        except Exception:
            if not ignore_errors:
                raise
            LOGGER.warning("Failed to publish live message.delta event.", exc_info=True)

    async def clear_active_run(self, *, thread_id: UUID, run_id: UUID) -> None:
        if self.controls is not None:
            await self.controls.clear_active_run(thread_id=thread_id, run_id=run_id)
