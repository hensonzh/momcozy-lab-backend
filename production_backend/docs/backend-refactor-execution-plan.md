# Production Backend Refactor Execution Plan

Status: backend-independent refactor complete; app codegen and integration
checks remain dependent on the Flutter refactor.

This document is the execution plan for the isolated production backend under
`production_backend/`. It complements the project-level
`production_backend/docs/product/momcozy-production-refactor-plan.md`, which explains the legacy system,
defects, target architecture, and full product migration.

## Current Answer

There is a complete project-level production refactor plan. The isolated
production backend has reached a stable independently testable boundary, so
remaining backend work is limited to generated Flutter client checks once the
App refactor owns codegen.

Backend refactor proceeded independently under these rules:

- A stable API/schema contract.
- Focused tests for the new backend module.
- No dependency on legacy `ChatSession`, `previous_response_id`, SQLite, or
  request-supplied `user_id` as an authority.
- A clear handoff contract for the future Flutter app integration.

## Source Documents

- `production_backend/docs/product/momcozy-production-refactor-plan.md`: system-level diagnosis and target
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
- Environment-controlled CORS middleware with explicit origins and production
  wildcard rejection.
- Environment-controlled trusted host middleware, required in production.
- Baseline security headers on API responses, with production-only HSTS.
- Structured request logs with `request_id`, route, status, and latency.
- Basic in-process request metrics exposed through `/v1/health/metrics`, with
  service-key protection in production.
- Environment-controlled fixed-window rate limiting with Redis-first counting,
  local fallback, health/docs exemptions, and stable `rate_limited` envelopes.
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
- Environment-controlled upload size limit with chunked route reads and service
  validation before object storage writes.
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
- Support tickets API: owner-scoped create/list/detail with explicit production
  DTOs, audit, and idempotent create.

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
- Streaming follow mode: the same SSE endpoint keeps replay as the default and
  supports explicit `follow=true` polling until terminal run events for app
  reconnect and live-consumption flows.
- Agent run worker skeleton: worker-owned run lock, cancel checks,
  queued/running/completed/failed/waiting transitions, assistant message
  persistence, stream cursor updates, and terminal cleanup around an injectable
  LangGraph/SDK handler.
- Agent runtime executor: validates graph/runtime pattern, reconstructs model
  input from the internal message ledger and cache-stable context projection,
  invokes an injectable OpenAI Agents SDK runner, and returns typed run outcomes
  without provider session state.
- SDK action proposal wiring: action proposals returned by the SDK node are
  persisted as `confirmation_required` actions, emit
  `action.confirmation_required`, and move the run into
  `waiting_for_confirmation` instead of failing with a placeholder error.
- OpenAI Agents SDK backend: default SDK runner now lazily imports the official
  `agents.Agent` / `agents.Runner.run` shape, maps `final_output` into typed
  runtime results, and validates `OPENAI_API_KEY` / `OPENAI_MODEL` through
  environment-managed settings when the agent worker is enabled.
- Replay bundle service: exports run, message, event, tool, action, artifact,
  runtime state, and safety ledger snapshots for incident debugging and eval
  seed material, with message content redacted by default.
- Eval seed service: creates draft `agent_eval_cases` from redacted replay
  bundles, preserving source run ID, event sequence expectations, tool call
  summaries, action statuses, and safety decision.
- Service-key protected admin endpoints: `/v1/agent/admin/runs/{run_id}/replay`
  exports replay bundles and `/v1/agent/admin/runs/{run_id}/eval-cases` creates
  draft eval cases from production-like runs.
- Agent run queue worker: scans durable queued runs plus recoverable stale
  running runs from Postgres and delegates each run to the lifecycle worker
  behind Redis run locks.
- Agent worker process entry: environment-controlled worker script and optional
  compose `workers` profile keep API and run execution as separate deployable
  processes, disabled by default until a real runtime handler is configured.
- Agent worker capacity: `AGENT_RUNTIME_WORKER_CONCURRENCY` controls bounded
  async run slots inside one worker process; each run uses an independent DB
  session and still acquires the Redis run lock, so capacity can scale by both
  worker replicas and per-process slots.
- Agent action confirmation now enqueues a durable `agent.action.apply` outbox
  job and emits `action.queued` with the persisted outbox job ID instead of an
  event-only queued marker.
- Agent action outbox apply skeleton: registered apply handlers can move actions
  through `applying -> applied` and emit `action.applied`; missing or permanent
  handler failures mark actions failed and emit `action.failed`.
- Support ticket agent action handler: confirmed `support.ticket.create` actions
  apply through `SupportTicketsService`, preserving owner scope, idempotency, and
  audit instead of writing support tables from agent code.
- Outbox worker process entry: environment-controlled worker script and optional
  compose `workers` profile process file cleanup and confirmed agent action
  jobs separately from API and agent run workers.
- SDK tool input schemas: tool contracts now resolve `input_schema_ref` to
  explicit JSON Schema before being exposed to the OpenAI Agents SDK, so models
  no longer receive unconstrained `additionalProperties: true` tool parameters.
- Agent business context tool: `business.context.read` exposes bounded,
  owner-scoped read summaries for records, plans, diary, and devices through
  service-layer permissions instead of session-state snapshots.
- Agent tool lifecycle events: guarded tool execution now persists
  `tool.started`, `tool.completed`, and `tool.failed` application events
  alongside tool call/output ledger rows for stream replay and debugging.
- Agent event sink: runtime components can write application events through a
  shared sink that also advances the Redis stream cursor, keeping DB replay and
  transient reconnect state aligned across API, agent worker, and outbox worker
  processes.
- Agent action policy: SDK and tool action proposals are validated against an
  explicit allowlist before any `agent_actions` row is created, so unsupported
  action types cannot drift into the outbox as late failures.
- SDK artifact persistence: artifacts returned by the SDK node are persisted in
  `agent_artifacts` and emit `artifact.created` application events before the
  run reaches its terminal or waiting state.
- SDK action proposal cardinality guard: each run accepts at most one action
  proposal, validated before artifacts or actions are persisted, so the runtime
  never silently drops extra model-proposed side effects.
- Agent action idempotency alignment: confirmation without an explicit
  idempotency key now persists the generated `agent-action:{action_id}` key on
  both the action and outbox job.
- Agent action stream events now include user-visible `preview_payload` and
  merge metadata while keeping `apply_payload` server-side only.
- Agent active-run guard: run creation now rejects a second non-terminal run on
  the same thread at the service layer, with a PostgreSQL partial unique index
  as the final concurrency guard.
- Agent run idempotency hygiene: if a newly reserved run idempotency key is
  blocked by an existing active run, the reservation is released so retries do
  not get stuck behind an `idempotency_in_progress` record.
- Agent thread activity ordering: persisted user messages touch
  `agent_threads.updated_at`, keeping thread lists ordered by recent activity.
- Tool input contract enforcement: registered JSON Schemas are validated before
  tool calls are persisted or executed, so unknown model-supplied parameters do
  not enter the tool ledger.
- SDK context serialization: structured state and business-fact projections are
  flattened as deterministic JSON for cache-stable SDK inputs.

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
- Safety observability foundation: deterministic safety guard decisions are
  counted by category, decision, and severity without storing user text or
  evidence payloads in the metrics surface.

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
- Contract tests and sample API flows. Done; OpenAPI snapshot, core path,
  stream-token, idempotency header, smoke-flow method/path, and auth-boundary
  tests are in place.

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
- explicit CORS configuration for browser/admin clients
- trusted host validation for production deployments
- baseline security response headers
- request/error contract
- structured request logging and basic metrics
- environment-controlled rate limiting for non-exempt API paths
- Docker/compose local backend environment
- DB/Alembic, Redis, and object storage foundation
- user/auth identity schema, signup/login/refresh/logout API, JWT
  `CurrentUser`, service key boundary, refresh token/session schema, and service
  actor audit attribution
- owner-scoped files, profiles, records, plans, diary, devices, notifications,
  and support modules
- file upload size limits controlled by environment variables
- audit/idempotency/outbox foundations
- durable agent runtime ledger, Redis controls, LangGraph/OpenAI Agents SDK
  boundaries, guarded tool executor, SSE replay, action confirmation, and
  deterministic safety gate
- non-PII agent safety metrics for allow/block/escalate decisions
- worker-owned Agent run lifecycle skeleton with lock/cancel/status/event
  semantics ready for LangGraph/SDK execution
- Agent runtime executor that bridges durable run/message ledger, context
  projection, tool metadata, and OpenAI Agents SDK runner results
- SDK action proposals now become durable confirmation-required actions and
  waiting run outcomes
- OpenAI Agents SDK backend wiring with environment-managed API key and model
  configuration for the agent worker
- growth record partial update and plan task partial update endpoints, closing
  the legacy growth revise and plan revise-task route mappings
- replay bundle export service for debugging and eval seed generation, default
  redacted to avoid accidental PII exposure, including artifact and runtime
  state snapshots
- eval seed service that turns replay bundles into draft regression cases linked
  back to the source run
- service-key protected replay/eval admin API and updated OpenAPI snapshot
- durable agent run queue scanner that can resume queued/stale running runs
  without in-memory session state
- environment-controlled agent worker process entry and compose profile for
  separate API/worker deployment
- durable outbox enqueue for confirmed agent actions, preserving action status
  and outbox status as separate contracts
- agent action outbox apply handler skeleton with idempotent applied handling,
  explicit missing-handler failure, and persisted action events
- first business action apply handler for `support.ticket.create`, routed
  through the support service layer
- environment-controlled outbox worker process entry wired to file cleanup and
  support-ticket agent action apply handlers
- explicit SDK tool parameter schemas for profile context reads and support
  ticket action proposals, keeping owner scope in backend auth rather than
  model-supplied user IDs
- bounded read-only business context tool for migrated records, plans, diary,
  and device facts
- read-only lactation status tool contract `records.milk_status.read` for
  deterministic milk-management coverage, trend, and observation flags
- application-level tool lifecycle events emitted by the ToolExecutor, keeping
  persisted SSE/replay streams aligned with the tool ledger
- shared AgentEventSink wiring in runtime/tool/action workers so Redis stream
  cursor updates follow persisted application events across processes
- action proposal allowlist for `support.ticket.create`, rejecting unsupported
  SDK/tool proposals before persistence
- SDK artifact persistence with replayable `artifact.created` events
- single-action proposal guard that rejects ambiguous multi-action SDK results
  before persistence
- generated action idempotency keys stored consistently on confirmed actions and
  outbox jobs
- optional follow mode for agent SSE streams, preserving default replay
  semantics while allowing clients to wait for terminal run events
- active-run guard for agent threads, backed by a PostgreSQL partial unique
  index over queued/running/waiting runs
- idempotency reservations for blocked agent run creation are released instead
  of becoming stale in-progress records
- agent threads are touched when new user messages are persisted, keeping
  conversation lists aligned with activity
- agent tool inputs are validated against registered JSON Schemas before
  tool-call persistence or execution
- structured agent context is serialized deterministically when flattened for
  the OpenAI Agents SDK boundary
- public agent action responses expose preview/status metadata only; server-side
  `apply_payload` and action idempotency keys remain internal
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
  records/plans, idempotency, agent event/SSE replay, and optional SSE follow
  mode.
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

Remaining App-dependent backend PR slices:

1. Add generated Flutter client CI job once the app refactor owns codegen.
2. Add generated client contract checks after Flutter codegen exists.
