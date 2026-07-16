from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


RUN_STATUSES = ("queued", "running", "waiting_for_confirmation", "completed", "failed", "cancelled", "expired")
ACTIVE_RUN_STATUSES = ("queued", "running", "waiting_for_confirmation")
ACTION_STATUSES = ("proposed", "confirmation_required", "confirmed", "applying", "applied", "rejected", "failed", "expired")
TOOL_CALL_STATUSES = ("started", "completed", "failed", "skipped", "blocked", "timed_out")
WORKFLOW_STATE_STATUSES = ("collecting", "ready", "waiting", "paused", "completed", "expired", "failed")
MEMORY_TYPES = ("user_preference", "stable_care_preference", "communication_preference", "recurring_constraint")
MEMORY_STATUSES = ("active", "archived", "deleted", "expired")


class AgentThread(Base):
    __tablename__ = "agent_threads"
    __table_args__ = (
        Index("ix_agent_threads_owner_updated", "owner_user_id", "updated_at"),
        Index("ix_agent_threads_owner_status_updated", "owner_user_id", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_thread_started", "thread_id", "started_at"),
        Index("ix_agent_runs_actor_status_started", "actor_user_id", "status", "started_at"),
        Index(
            "ix_agent_runs_runnable_created",
            "status",
            "created_at",
            "id",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_agent_runs_request_id", "request_id"),
        Index("ix_agent_runs_trace_id", "trace_id"),
        Index("ix_agent_runs_service_skill_started", "service_skill_id", "started_at"),
        Index(
            "uq_agent_runs_thread_active",
            "thread_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'waiting_for_confirmation')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_threads.id"), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued", nullable=False)
    runtime_pattern: Mapped[str] = mapped_column(String(64), default="sdk_only", server_default="sdk_only", nullable=False)
    runtime_version: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    service_skill_id: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    routing_source: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    routing_confidence_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    routing_summary: Mapped[dict[str, Any]] = mapped_column(
        "routing_summary_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    request_id: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    trace_id: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    error_code: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    error_details: Mapped[dict[str, Any]] = mapped_column(
        "error_details_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AgentRoutingDecision(Base):
    __tablename__ = "agent_routing_decisions"
    __table_args__ = (
        UniqueConstraint("run_id", "message_id", name="uq_agent_routing_decisions_run_message"),
        Index("ix_agent_routing_decisions_run_created", "run_id", "created_at"),
        Index("ix_agent_routing_decisions_thread_created", "thread_id", "created_at"),
        Index("ix_agent_routing_decisions_service_skill_created", "selected_skill_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_threads.id"), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    message_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_messages.id"), nullable=False)
    selected_skill_id: Mapped[str] = mapped_column(String(80), nullable=False)
    routing_source: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(40), default="passthrough", server_default="passthrough", nullable=False)
    intents: Mapped[list[Any]] = mapped_column(
        "intents_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    reason_codes: Mapped[list[Any]] = mapped_column(
        "reason_codes_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    safety_flags: Mapped[list[Any]] = mapped_column(
        "safety_flags_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    needs_clarification: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    tool_scope_version: Mapped[str] = mapped_column(String(80), default="default", server_default="default", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_agent_messages_thread_sequence"),
        Index("ix_agent_messages_thread_sequence", "thread_id", "sequence"),
        Index("ix_agent_messages_run_created", "run_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_threads.id"), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), default="text", server_default="text", nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(
        "content_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), default="completed", server_default="completed", nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        UniqueConstraint("run_id", "call_id", name="uq_agent_tool_calls_run_call_id"),
        Index("ix_agent_tool_calls_run_status", "run_id", "status"),
        Index("ix_agent_tool_calls_run_tool", "run_id", "tool_name"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    call_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="started", server_default="started", nullable=False)
    safe_args: Mapped[dict[str, Any]] = mapped_column(
        "safe_args_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    error_code: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentToolOutput(Base):
    __tablename__ = "agent_tool_outputs"
    __table_args__ = (Index("ix_agent_tool_outputs_tool_call", "tool_call_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tool_call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_tool_calls.id"), nullable=False)
    safe_output: Mapped[dict[str, Any]] = mapped_column(
        "safe_output_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    raw_output_ref: Mapped[str] = mapped_column(String(512), default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_events_run_sequence"),
        Index("ix_agent_events_run_sequence", "run_id", "sequence"),
        Index("ix_agent_events_thread_created", "thread_id", "created_at"),
        Index("ix_agent_events_type_created", "event_type", "created_at"),
    )

    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_threads.id"), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        "payload_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"
    __table_args__ = (
        Index("ix_agent_artifacts_run_status", "run_id", "status"),
        Index("ix_agent_artifacts_owner_type_created", "owner_user_id", "artifact_type", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), default="v1", server_default="v1", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="created", server_default="created", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        "payload_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    raw_payload_ref: Mapped[str] = mapped_column(String(512), default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AgentAction(Base):
    __tablename__ = "agent_actions"
    __table_args__ = (
        Index("ix_agent_actions_run_status", "run_id", "status"),
        Index("ix_agent_actions_actor_status", "actor_user_id", "status"),
        Index("ix_agent_actions_idempotency_key", "idempotency_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    target_id: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="proposed", server_default="proposed", nullable=False)
    side_effect_level: Mapped[str] = mapped_column(String(32), default="medium", server_default="medium", nullable=False)
    preview_payload: Mapped[dict[str, Any]] = mapped_column(
        "preview_payload_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    apply_payload: Mapped[dict[str, Any]] = mapped_column(
        "apply_payload_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    error_code: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AgentWorkflowState(Base):
    __tablename__ = "agent_workflow_states"
    __table_args__ = (
        Index("ix_agent_workflow_states_thread_status", "thread_id", "status"),
        Index("ix_agent_workflow_states_owner_type_status", "owner_user_id", "workflow_type", "status"),
        Index("ix_agent_workflow_states_run_created", "run_id", "created_at"),
        Index("ix_agent_workflow_states_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_threads.id"), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True)
    workflow_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="collecting", server_default="collecting", nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), default="v1", server_default="v1", nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(
        "state_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    active_step: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    step_token: Mapped[str] = mapped_column(String(128), default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AgentContextProjection(Base):
    __tablename__ = "agent_context_projections"
    __table_args__ = (
        Index("ix_agent_context_projections_run_created", "run_id", "created_at"),
        Index("ix_agent_context_projections_thread_created", "thread_id", "created_at"),
        Index("ix_agent_context_projections_workflow", "active_workflow_state_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_threads.id"), nullable=False)
    context_schema_version: Mapped[str] = mapped_column(String(80), default="v1", server_default="v1", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    tool_schema_version: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    selected_message_ids: Mapped[list[Any]] = mapped_column(
        "selected_message_ids_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    active_workflow_state_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_workflow_states.id"),
        nullable=True,
    )
    source_refs: Mapped[dict[str, Any]] = mapped_column(
        "source_refs_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    projection_summary: Mapped[dict[str, Any]] = mapped_column(
        "projection_summary_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    token_estimate: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentRunSummary(Base):
    __tablename__ = "agent_run_summaries"
    __table_args__ = (
        UniqueConstraint("run_id", "summary_type", name="uq_agent_run_summaries_run_type"),
        Index("ix_agent_run_summaries_thread_created", "thread_id", "created_at"),
        Index("ix_agent_run_summaries_owner_skill_created", "owner_user_id", "service_skill_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_threads.id"), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    service_skill_id: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    summary_type: Mapped[str] = mapped_column(String(40), default="run_fact", server_default="run_fact", nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), default="v1", server_default="v1", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        "payload_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    source_message_ids: Mapped[list[Any]] = mapped_column(
        "source_message_ids_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    source_tool_call_ids: Mapped[list[Any]] = mapped_column(
        "source_tool_call_ids_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "memory_key", name="uq_agent_memories_owner_memory_key"),
        Index("ix_agent_memories_owner_type_status", "owner_user_id", "memory_type", "status"),
        Index("ix_agent_memories_owner_updated", "owner_user_id", "updated_at"),
        Index("ix_agent_memories_source_run", "source_run_id"),
        Index("ix_agent_memories_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    memory_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True)
    source_message_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_messages.id"), nullable=True)
    memory_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), default="v1", server_default="v1", nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(
        "content_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    confidence_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AgentMemorySettings(Base):
    __tablename__ = "agent_memory_settings"
    __table_args__ = (Index("ix_agent_memory_settings_owner_updated", "owner_user_id", "updated_at"),)

    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AgentMemorySnapshot(Base):
    __tablename__ = "agent_memory_snapshots"

    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(80), default="v1", server_default="v1", nullable=False)
    items: Mapped[list[Any]] = mapped_column(
        "items_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    source_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    extractor_version: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AgentMemoryConsolidationRun(Base):
    __tablename__ = "agent_memory_consolidation_runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "source_date",
            "source_hash",
            "extractor_version",
            name="uq_agent_memory_consolidation_source",
        ),
        Index("ix_agent_memory_consolidation_date_status", "source_date", "status"),
        Index("ix_agent_memory_consolidation_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    source_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="extracting", server_default="extracting", nullable=False)
    input_message_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    upserted_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    archived_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    error_code: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AgentEvalCase(Base):
    __tablename__ = "agent_eval_cases"
    __table_args__ = (
        Index("ix_agent_eval_cases_suite_status", "suite", "status"),
        Index("ix_agent_eval_cases_domain_status", "domain", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    suite: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        "input_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    expected_behavior: Mapped[dict[str, Any]] = mapped_column(
        "expected_behavior_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    expected_tool_calls: Mapped[list[Any]] = mapped_column(
        "expected_tool_calls_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    source_run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", server_default="draft", nullable=False)
    owner_team: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
