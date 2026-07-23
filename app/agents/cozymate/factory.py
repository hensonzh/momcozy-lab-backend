from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.agent_runtime.actions.executor import AgentActionExecutor
from app.agent_runtime.context.facts import AgentFactRepository, AgentFactService
from app.agent_runtime.context.image_assets import AgentImageAccessService
from app.agent_runtime.context.memory.service import AgentMemoryRepository, AgentMemoryService
from app.agent_runtime.events.publisher import AgentEventPublisher
from app.agent_runtime.events.transient import AgentTransientStream
from app.agent_runtime.providers import create_agent_model_runner
from app.agent_runtime.runs.controls import AgentRunControls
from app.agent_runtime.runs.repository import AgentRuntimeRepository
from app.agent_runtime.runs.service import AgentRuntimeService
from app.core.metrics import RequestMetrics
from app.core.settings import Settings
from app.modules.assets.service import ProductAssetService
from app.modules.audit import AuditService, IdempotencyService
from app.modules.audit.repository import AuditRepository
from app.modules.diary.repository import DiaryRepository
from app.modules.diary.service import DiaryService
from app.modules.files.repository import FileRepository
from app.modules.notifications import NotificationsService
from app.modules.notifications.repository import NotificationsRepository
from app.modules.plans.repository import PlansRepository
from app.modules.plans.service import PlansService
from app.modules.profiles.lactation_context import LactationContextService
from app.modules.profiles.repository import ProfileRepository
from app.modules.profiles.service import ProfileService
from app.modules.records.repository import RecordsRepository
from app.modules.records.service import RecordsService
from app.modules.support import SupportTicketsService
from app.modules.support.repository import SupportTicketsRepository

from .actions import build_cozymate_action_handlers, cozymate_action_policy
from .context import BusinessFactsProjector
from .context.client import sanitize_cozymate_client_context
from .executor import CozymateAgentExecutor
from .tools import CozymateToolExecutor, build_default_tool_handlers, default_tool_registry


@dataclass(frozen=True)
class CozymateRuntimeComponents:
    repository: AgentRuntimeRepository
    executor: CozymateAgentExecutor
    action_executor: AgentActionExecutor
    transient_stream: AgentTransientStream


def build_cozymate_runtime(
    *,
    session: Any,
    settings: Settings,
    object_storage: Any,
    redis_client: Any,
    controls: AgentRunControls,
    metrics: RequestMetrics,
) -> CozymateRuntimeComponents:
    repository = AgentRuntimeRepository(session)
    fact_repository = AgentFactRepository(session)
    audit_repository = AuditRepository(session)
    file_repository = FileRepository(session)
    image_access_service = AgentImageAccessService(
        repository=repository,
        file_repository=file_repository,
        object_storage=object_storage,
        url_ttl=timedelta(seconds=settings.agent_image_signed_url_ttl_seconds),
    )
    memory_service = AgentMemoryService(repository=AgentMemoryRepository(session))
    fact_service = AgentFactService(
        repository=fact_repository,
        audit_service=AuditService(repository=audit_repository),
        memory_consent_reader=memory_service,
        extraction_enabled=False,
    )
    profile_repository = ProfileRepository(session)
    profile_service = ProfileService(
        repository=profile_repository,
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )
    records_service = RecordsService(
        repository=RecordsRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )
    lactation_context_service = LactationContextService(
        profile_repository=profile_repository,
        records_service=records_service,
        audit_service=AuditService(repository=audit_repository),
    )
    plans_service = PlansService(
        repository=PlansRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )
    diary_service = DiaryService(
        repository=DiaryRepository(session),
        audit_service=AuditService(repository=audit_repository),
    )
    notifications_service = NotificationsService(
        repository=NotificationsRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )
    support_service = SupportTicketsService(
        repository=SupportTicketsRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )
    action_policy = cozymate_action_policy()
    action_handlers = build_cozymate_action_handlers(
        notifications_service=notifications_service,
        plans_service=plans_service,
        profile_service=profile_service,
        records_service=records_service,
        diary_service=diary_service,
        support_service=support_service,
    )
    action_executor = AgentActionExecutor(
        repository=repository,
        handlers=action_handlers,
        action_policy=action_policy,
    )
    runtime_service = AgentRuntimeService(
        repository=repository,
        idempotency_service=IdempotencyService(repository=audit_repository),
        action_executor=action_executor,
        file_repository=file_repository,
        image_access_service=image_access_service,
        controls=controls,
        fact_service=fact_service,
        memory_service=memory_service,
        client_context_sanitizer=sanitize_cozymate_client_context,
        action_policy=action_policy,
    )
    tool_registry = default_tool_registry()
    tool_registry.validate_action_bindings(
        policy_action_types=action_policy.rules,
        handler_action_types=action_handlers,
    )
    transient_stream = AgentTransientStream(redis_client)
    event_sink = AgentEventPublisher(
        repository=repository,
        controls=controls,
        after_append=session.commit,
        transient_stream=transient_stream,
    )
    tool_handlers = build_default_tool_handlers(
        profile_service=profile_service,
        lactation_context_service=lactation_context_service,
        records_service=records_service,
        plans_service=plans_service,
        diary_service=diary_service,
        asset_service=ProductAssetService(),
        agent_runtime_service=runtime_service,
        object_storage=object_storage,
    )
    tool_executor = CozymateToolExecutor(
        registry=tool_registry,
        repository=repository,
        event_sink=event_sink,
        metrics=metrics,
        object_storage=object_storage,
        max_inline_output_bytes=settings.agent_runtime_max_inline_payload_bytes,
        handlers=tool_handlers,
        transient_stream=transient_stream,
    )
    model_runner = create_agent_model_runner(
        settings=settings,
        metrics=metrics,
        image_url_resolver=image_access_service.resolve_for_provider,
    )
    executor = CozymateAgentExecutor(
        repository=repository,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        event_sink=event_sink,
        business_facts_projector=BusinessFactsProjector(handlers=tool_handlers),
        transient_stream=transient_stream,
        fact_service=fact_service,
        sdk_runner=model_runner,
        object_storage=object_storage,
        max_inline_artifact_payload_bytes=settings.agent_runtime_max_inline_payload_bytes,
        action_policy=action_policy,
    )
    return CozymateRuntimeComponents(
        repository=repository,
        executor=executor,
        action_executor=action_executor,
        transient_stream=transient_stream,
    )
