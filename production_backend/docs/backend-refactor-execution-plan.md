# Production Backend Refactor Execution Plan

Status: active

This document is the execution plan for the isolated production backend under
`production_backend/`. It complements the project-level
`docs/momcozy-production-refactor-plan.md`, which explains the legacy system,
defects, target architecture, and full product migration.

## Current Answer

There is a complete project-level production refactor plan, but the backend also
needs this backend-specific execution plan so work can proceed without waiting
for the Flutter app rewrite.

Backend refactor can continue independently as long as each phase produces:

- A stable API/schema contract.
- Focused tests for the new backend module.
- No dependency on legacy `ChatSession`, `previous_response_id`, SQLite, or
  request-supplied `user_id` as an authority.
- A clear handoff contract for the future Flutter app integration.

## Source Documents

- `docs/momcozy-production-refactor-plan.md`: system-level diagnosis and target
  architecture.
- `production_backend/docs/backend-refactor-inventory.generated.md`: generated
  legacy route/table risk inventory.
- `production_backend/docs/backend-refactor-inventory.md`: inventory template
  and migration guardrails.
- `MomCozyApp/docs/flutter-refactor-review-issues.md`: Flutter-side blockers and
  deferred backend-contract dependencies.

## Target Backend Boundary

```text
FastAPI API
  -> dependencies / auth / request_id / error handlers
  -> module routers
  -> service / use case
  -> repository
  -> PostgreSQL via SQLAlchemy + Alembic
  -> Redis for reconstructable runtime controls
  -> object storage for files and artifacts
  -> workers for outbox and async effects
```

For agent features:

```text
FastAPI API
  -> AgentRuntimeService
  -> RunStore / EventStore / ActionStore / ArtifactStore
  -> LangGraph orchestration
  -> OpenAI Agents SDK runners inside graph nodes
  -> ToolExecutor guarded by permission/action/audit policy
  -> Business services and repositories
```

The new backend will not keep the legacy Responses API loop, provider
`previous_response_id`, or a compatibility fallback to old `ChatSession`.

## Phase Plan

### Phase 0: Isolation And Inventory

Goal: create a clean backend workspace and freeze legacy risk.

Completed:

- Isolated `production_backend/` directory.
- App factory and health shell.
- Legacy backend route/table inventory generator.
- Generated inventory of 65 routes, 20 SQLite tables, and high-risk route flags.

Acceptance:

- New production code does not import `momcozy_agent`.
- Inventory can be regenerated before each migration wave.

### Phase 1: Backend Foundation

Goal: production FastAPI shell independent of legacy backend.

Completed:

- Typed settings and environment contract.
- Request ID middleware.
- Stable error envelope.
- SQLAlchemy async engine/session factory.
- Alembic migration shell.
- Redis lifecycle registration.
- Object storage abstraction with local development implementation.
- S3-compatible managed object storage provider.
- Production validation rejects implicit localhost DB/Redis.
- `/v1/health/ready` can validate DB and Redis readiness.

Remaining:

- Structured logging and basic metrics.
- Docker/compose local environment.

Acceptance:

- Local development can start with local Postgres/Redis/object storage.
- Production cannot start with implicit local infrastructure.
- `/v1/health/ready` fails when required infrastructure is unavailable.

### Phase 2: Identity And Access Boundary

Goal: remove request-supplied `user_id` as an authority.

Completed:

- `users` and `auth_identities` models and migration.
- JWT access-token verification dependency.
- `CurrentUser` object for backend-derived owner scope.
- `device_sessions` and `refresh_tokens` models and migration.

Remaining:

- Refresh token rotation and reuse detection.
- Logout/session revocation.
- RBAC/permission policy helpers.
- Service-to-service key separated from user auth.

Acceptance:

- User-facing routes derive owner scope from `CurrentUser`.
- Missing/invalid/expired/revoked auth has stable error codes.
- Cross-user reads/writes have regression tests.

### Phase 3: Files, Audit, Idempotency, And Outbox

Goal: build the cross-cutting machinery required before migrating write APIs.

Completed:

- Owner-scoped `files` metadata model and migration.
- File upload and file detail API using `CurrentUser` owner scope.
- `audit_logs` model and migration.
- `idempotency_keys` model and migration.
- `outbox_jobs` model and migration.
- Audit and idempotency services.
- Outbox repository/service and worker skeleton with retry/dead-letter semantics.
- File upload uses optional `Idempotency-Key`, request ID, and audit recording.

Remaining:

- File listing, deletion, and object-storage cleanup flows.
- Route-level audit/idempotency integration for additional migrated writes.
- API-level `Idempotency-Key` handling for retryable writes.

Acceptance:

- File uploads are owner-scoped and store bytes through object storage.
- Retryable write endpoints can distinguish replay from conflict.
- Critical writes record actor, request ID, resource, outcome, and details.

### Phase 4: Core Product Business Modules

Goal: migrate deterministic product APIs before agent runtime.

Suggested order:

1. Profile and infant profile.
2. Files and media.
3. Records: feeding, pumping, growth.
4. Plans and tasks.
5. Pregnancy diary.
6. Devices and pump telemetry.
7. Notifications and support tickets.

Rules:

- Each module owns models, schemas, repository, service, router, permissions, and
  tests.
- Each public route has request/response/error schema and OpenAPI coverage.
- Each repository method encodes owner scope by default.
- Each migrated write path uses permission, transaction, idempotency where
  retryable, and audit where relevant.

Acceptance:

- No migrated route trusts query/body `user_id`.
- No migrated route calls legacy `data_store`.
- No migrated route returns legacy raw error shapes.

### Phase 5: Agent Runtime Ledger

Goal: create durable agent runtime primitives before implementing model loop.

Required tables:

- `agent_threads`
- `agent_runs`
- `agent_messages`
- `agent_tool_calls`
- `agent_events`
- `agent_artifacts`
- `agent_actions`
- `agent_context_checkpoints`

Rules:

- Store full internal message ledger; do not depend on provider session state.
- Store event sequence for replay/reconnect.
- Store run status including waiting states.
- Redis stores only active lock, cancel flag, stream cursor, and cache.

Acceptance:

- A run can be created, cancelled, resumed, and replayed without model calls.
- Event contract is typed and tested.

### Phase 6: Agent Runtime Implementation

Goal: implement production agent using LangGraph + OpenAI Agents SDK.

Pattern decision:

- Use LangGraph for durable orchestration, checkpoint, interrupt, resume, and
  workflow edges.
- Use OpenAI Agents SDK inside graph nodes for specialist agent/tool loops,
  guardrails, and tracing.
- Do not keep the legacy Responses API loop or old adapter fallback.

Required components:

- `AgentRuntimeService`
- graph definition and checkpoint store
- SDK runner wrapper
- tool registry metadata
- tool executor with permission/action/audit guard
- context projector
- stream adapter
- replay/eval harness

Acceptance:

- No `previous_response_id`.
- No in-memory production session.
- Tool calls, tool results, actions, artifacts, and events are persisted.
- Non-blocking side effects use action/outbox effect lane.

### Phase 7: Safety, Observability, And Eval

Goal: make the backend support real-user operations.

Required:

- Medical/emotional red-flag deterministic guard.
- Prompt injection and tool permission checks.
- Structured logs with request_id/run_id/thread_id.
- Metrics for latency, errors, tokens, tool success, cancellation.
- Trace model calls, DB calls, tool calls, and workers.
- Golden evals for health safety, permission bypass, tool correctness, and
  replay regressions.

Acceptance:

- High-risk cases are blocked, escalated, or safely redirected before normal
  business flow.
- Production incidents can become regression tests.

### Phase 8: App Integration Contract

Goal: hand the Flutter app a stable backend instead of legacy compatibility.

Deliverables:

- OpenAPI schema.
- Auth/session contract.
- Error envelope contract.
- File upload contract.
- Records/plans/diary/devices contracts.
- Agent event/action/artifact contract.
- Contract tests and sample API flows.

Acceptance:

- Flutter repositories are generated from or validated against the new backend
  schema.
- No App code needs to parse legacy raw responses.
- Tokens do not appear in URLs.

## Current Progress Snapshot

Completed backend commits have established:

- isolated backend workspace
- inventory tooling
- environment contract
- request/error contract
- DB/Alembic foundation
- Redis foundation
- object storage abstraction
- user/auth identity schema
- JWT `CurrentUser` dependency
- owner-scoped file metadata
- audit/idempotency schema

Next recommended backend PR slices:

1. Fix production startup blockers: managed object storage, explicit production
   DB/Redis URLs, and real readiness checks.
2. Add device sessions and refresh token rotation tables.
3. Add repository/service/router for owner-scoped file upload.
4. Add audit/idempotency services and outbox model.
5. Migrate the first deterministic business module.
