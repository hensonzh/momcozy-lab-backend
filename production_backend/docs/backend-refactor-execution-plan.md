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
- Structured request logs with `request_id`, route, status, and latency.
- Basic in-process request metrics exposed through `/v1/health/metrics`.
- Docker/compose local environment for API, Postgres, Redis, migrations, and
  local object storage volume.

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
- Refresh token service with hash-only storage, rotation, reuse detection, and
  session revocation.
- Permission policy helper for explicit permissions, admin role bypass, and
  owner-scope checks.
- Internal service key dependency is separate from `CurrentUser` user auth.
- Email/password signup and login API surface.
- Access token issuing, refresh token rotation API, and logout session
  revocation API.
- Service-key initiated writes can be attributed in audit logs through
  `actor_type=service` and `actor_service`.

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
- File listing and soft deletion are owner-scoped; deletion queues object cleanup
  through outbox.
- File object cleanup has an outbox handler registered through the worker handler
  registry.
- Shared API dependency normalizes `Idempotency-Key` for retryable writes across
  files, profiles, records, plans, devices, notifications, support, and agent
  runtime routes.

Acceptance:

- File uploads are owner-scoped and store bytes through object storage.
- Retryable write endpoints can distinguish replay from conflict.
- Critical writes record actor, request ID, resource, outcome, and details.

### Phase 4: Core Product Business Modules

Goal: migrate deterministic product APIs before agent runtime.

Completed:

- Profile module foundation: `user_profiles` and `infant_profiles` models,
  migration, owner-scoped profile/infant APIs, audit on writes, and
  idempotent infant creation.
- Records data foundation: owner-scoped `feeding_records`, `pumping_records`,
  and `growth_records` models and migration.
- Feeding record API: owner-scoped create/list/delete with infant ownership
  validation, audit, and idempotent create.
- Pumping record API: owner-scoped create/list/delete with audit and idempotent
  create.
- Growth record API: owner-scoped create/list/delete with infant ownership
  validation, audit, and idempotent create.
- Plans data foundation: owner-scoped `plans` and `plan_tasks` models and
  migration.
- Plans/tasks API: owner-scoped create/list/detail/delete for plans and
  create/list/complete/delete for tasks, with audit and idempotent creates.
- Pregnancy diary data foundation: owner/date-scoped diary entries and linked
  health notes.
- Pregnancy diary API: owner-scoped list/get/upsert/delete by entry date with
  audit on writes.
- Devices data foundation: owner-scoped pump device metadata and pump telemetry
  events.
- Devices API: owner-scoped pump upsert/list and telemetry ingest/list with
  audit and idempotent telemetry ingest.
- Notifications/support data foundation: owner-scoped notifications and support
  tickets.
- Notifications API: service-key protected notification creation plus
  owner-scoped inbox/list/read/archive APIs.
- Support tickets API: owner-scoped create/list/detail with legacy ticket-object
  input compatibility, audit, and idempotent create.

Suggested order:

1. Phase 5 agent runtime ledger.

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

Completed:

- Agent runtime ledger data foundation: owner-scoped threads, runs, messages,
  tool calls/outputs, events, artifacts, actions, and context checkpoints with
  replay indexes and no provider session state columns.
- Agent runtime ledger API: owner-scoped thread creation/list/detail, run
  creation with persisted user message and replayable events, idempotent run
  creation, run detail, event replay, and idempotent cancellation without model
  calls.
- Agent runtime Redis controls: active run, cancel flag, stream cursor, and
  cooperative run lock helpers kept as reconstructable transient state.

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

Completed:

- Agent runtime implementation boundary: LangGraph + OpenAI Agents SDK
  dependency contract, graph version registry/state shape, SDK runner adapter
  boundary, tool contract registry, and context builder ordered for prompt cache
  stability.
- Agent action manager foundation: owner-scoped action read/confirm/reject,
  `confirmation_required -> confirmed` transition, `action.queued` event with
  `outbox_status=queued`, and no `queued` action status.
- Tool executor foundation: contract lookup, permission and actor owner-scope
  enforcement, safe args/result redaction, persisted tool call/output, and
  structured failure mapping.
- Streaming replay endpoint: `/v1/agent/runs/{run_id}/stream` emits persisted
  application event envelopes as SSE, never provider raw events.
- Agent run worker skeleton: worker-owned run lock, cancel checks,
  queued/running/completed/failed/waiting transitions, assistant message
  persistence, stream cursor updates, and terminal cleanup around an injectable
  LangGraph/SDK handler.
- Agent runtime executor: validates graph/runtime pattern, reconstructs model
  input from the internal message ledger and cache-stable context projection,
  invokes an injectable OpenAI Agents SDK runner, and returns typed run outcomes
  without provider session state.
- Agent run queue worker: scans durable queued runs plus recoverable stale
  running runs from Postgres and delegates each run to the lifecycle worker
  behind Redis run locks.

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

Completed:

- Safety/eval data foundation: `agent_safety_events` and `agent_eval_cases`
  with owner/run linkage, evidence payloads, suite/status indexes, and source
  run references for incident-to-regression workflows.
- Deterministic safety guard foundation: emotional crisis, maternal/baby health
  red flags, and prompt injection rule gates with persisted non-allow safety
  decisions.
- Agent run safety gate integration: unsafe user messages persist the user
  message and safety event, emit `safety.blocked` and `run.failed`, and do not
  queue model work.
- Worker/runtime observability foundation: request metrics now include outbox
  job outcomes, agent tool outcomes, and OpenAI Agents SDK node outcomes without
  recording prompt text, tool args, job payloads, or secrets.

### Phase 8: App Integration Contract

Goal: hand the Flutter app a stable backend instead of legacy compatibility.

Deliverables:

- OpenAPI schema. Done: `production_backend/docs/openapi.generated.json`.
- Auth/session contract. Done in OpenAPI and
  `production_backend/docs/api-contract-handoff.md`.
- Error envelope contract. Done in handoff document and error contract tests.
- File upload contract. Done in OpenAPI and handoff document.
- Records/plans/diary/devices contracts. Done in OpenAPI snapshot.
- Agent event/action/artifact contract. Done in OpenAPI snapshot and handoff
  document.
- Contract tests and sample API flows. In progress; OpenAPI snapshot, core path,
  stream-token, and idempotency header tests are in place.

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
- structured request logging and basic metrics
- Docker/compose local backend environment
- DB/Alembic, Redis, and object storage foundation
- user/auth identity schema, signup/login/refresh/logout API, JWT
  `CurrentUser`, service key boundary, refresh token/session schema, and service
  actor audit attribution
- owner-scoped files, profiles, records, plans, diary, devices, notifications,
  and support modules
- audit/idempotency/outbox foundations
- durable agent runtime ledger, Redis controls, LangGraph/OpenAI Agents SDK
  boundaries, guarded tool executor, SSE replay, action confirmation, and
  deterministic safety gate
- worker-owned Agent run lifecycle skeleton with lock/cancel/status/event
  semantics ready for LangGraph/SDK execution
- Agent runtime executor that bridges durable run/message ledger, context
  projection, tool metadata, and OpenAI Agents SDK runner results
- durable agent run queue scanner that can resume queued/stale running runs
  without in-memory session state
- OpenAPI schema export script, generated schema snapshot, and API handoff
  document for Flutter integration
- Auth/token API contract tests for token response shape, validation envelope,
  invalid credentials envelope, logout Bearer requirement, and refresh-token
  body placement
- Deployment runbook and release smoke checklist covering infrastructure,
  migrations, auth, core APIs, agent runtime, observability, worker backlog,
  agent recovery, rollback, and security incidents.
- CI workflow for production backend tests, Alembic head check, OpenAPI snapshot
  drift, legacy runtime reference scan, compose validation, and container build.
- Flutter integration smoke fixtures for auth/session, file upload,
  records/plans, idempotency, and agent event/SSE replay.
- Empty database migration smoke test using Alembic offline `upgrade head --sql`
  and CI coverage for the generated DDL path.
- Deployment runbook now includes backup/restore and credential rotation drills
  that rely on environment-variable managed infrastructure and secret-manager
  rotation.
- Flutter generated-client compatibility notes document source-of-truth
  contracts, regeneration rules, breaking-change policy, mobile token handling,
  idempotency, and agent stream reducer requirements.
- Production backend ruff/mypy configuration and CI lint/type gates scoped to
  `production_backend/`.
- Postgres integration profile and CI `postgres-migration` job that upgrades a
  live Postgres service to Alembic head and verifies critical tables.
- Backup/restore automation hook manifest and CI check for environment-driven
  managed Postgres/object-storage restore drills.
- Redis runtime profile and CI `redis-runtime-controls` job that verifies live
  Redis active-run, cancel, stream cursor, and exclusive lock semantics.
- Object storage integration profile and CI `object-storage-integration` job
  that verifies S3-compatible put/get/delete semantics against MinIO.
- Tighter mypy gates for incomplete definitions, untyped calls, implicit
  optionals, generic parameters, redundant casts, unused ignores, and equality
  checks.
- Repository and app-state return typing cleanup so `warn_return_any` is now a
  backend CI gate.
- Remaining helper annotations completed so `disallow_untyped_defs` is now a
  backend CI gate.

Next recommended backend PR slices:

1. Add generated Flutter client CI job once the app refactor owns codegen.
2. Add generated client contract checks after Flutter codegen exists.
