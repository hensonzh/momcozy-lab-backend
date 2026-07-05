# Legacy Backend Acceptance Loop

Status: draft operating guide

This document turns the legacy backend inventory into an engineering loop:

```text
inventory legacy behavior
-> define target contract
-> write tests and evals first
-> let Codex implement the slice
-> run gates until acceptance passes
-> self-review and commit
```

It complements:

- `backend-refactor-inventory.generated.md`: generated list of legacy routes,
  SQLite tables, direct `data_store` calls, and risk flags.
- `production_backend/docs/product/momcozy-production-refactor-plan.md`: system diagnosis, target
  architecture, state model, and migration plan.
- `production_backend/docs/product/momcozy-agent-service-test-plan.md`: product-level Agent service
  behavior and safety acceptance.
- `api-contract-handoff.md` and `openapi.generated.json`: target backend API
  contracts for the Flutter app.

## Why This Loop Exists

The legacy backend contains many working demo behaviors, but they are not
production contracts. Before Codex migrates a slice, the team should freeze the
behavior that matters and express it as tests, evals, and review gates.

The loop prevents three common failure modes:

- copying unsafe legacy behavior such as request-supplied `user_id`;
- shipping code that passes happy-path tests but breaks idempotency, replay,
  owner scope, or action/audit requirements;
- treating Agent quality as untestable natural-language behavior instead of
  a mix of deterministic contracts, golden evals, and replay checks.

## Source Inventory

The generated legacy inventory currently reports:

```text
routes: 65
routes with direct data_store calls: 36
routes with migration risk flags: 55
SQLite tables: 20
tables without owner scope column: 1
inline ALTER TABLE statements: 2
```

Before each migration wave, regenerate it:

```bash
cd MomCozyAgent
production_backend/.venv/bin/python production_backend/scripts/inventory_legacy_backend.py --write
```

Acceptance for the inventory step:

- generated inventory is current;
- every route in the slice has a target owner, module, and disposition;
- every write path is marked direct write, action proposal, or async outbox;
- every legacy table used by the slice maps to a PostgreSQL model or is
  explicitly retired.

## Codex Work Loop

Use this loop for each PR-sized migration slice.

### 1. Select A Slice

Pick one domain or one narrow vertical path:

```text
files upload
feeding records
pregnancy diary
support ticket action
agent run streaming
voice session
```

Do not mix unrelated domains in the same loop unless the shared dependency is
the actual goal, such as idempotency or audit.

### 2. Freeze The Experience Main Flow

Start from the user's main path through the feature, not from the endpoint.
The experience flow defines what a successful product moment looks like before
it is decomposed into APIs, state changes, events, tools, or workers.

For each feature, write the happy path and the important detours:

```text
entry point
user intent
preconditions and required data
first screen or first assistant response
clarification points
confirmation points
backend operations
stream/action/UI events
visible success state
empty/offline/error states
undo/cancel/retry behavior
postconditions in persisted data
support or safety escalation, if relevant
```

Examples:

```text
upload pump session record
  user opens Records -> adds pumping amount/time -> sees saved record
  -> today summary and trend queries include it
  -> retry with same idempotency key does not duplicate it

create support ticket from Agent
  user reports device fault -> Agent asks only needed details
  -> shows confirmation card -> user confirms
  -> action.queued appears -> ticket is created by outbox
  -> action.applied and ticket detail are replayable

pregnancy diary today entry
  user opens today diary -> sees empty or existing entry
  -> edits mood/content -> save succeeds
  -> deleting hides the entry
  -> saving same date again restores the soft-deleted row
```

Experience-flow acceptance must verify what the user can observe and what the
system persists. A flow is not accepted just because individual endpoints return 200.

### 3. Freeze The Target Contract

After the experience flow is clear, define the endpoint or internal operation
contracts needed to support it:

```text
route / operation
request schema
response schema
error envelope
auth requirement
owner scope
permission rule
idempotency rule
side effects
audit requirement
stream/event contract, if any
worker/outbox behavior, if any
```

The target contract wins over the legacy response shape. Legacy raw responses
such as `{error: -1}` are reference material only.

### 4. Write Acceptance Before Implementation

Every slice should start with tests or eval cases that fail for the missing
target behavior.

Minimum experience-flow test layers:

```text
main happy path smoke test
empty state test
permission or owner-scope failure from the user's path
retry/idempotency behavior from the user's path
visible event/UI state sequence for Agent or streaming flows
persisted postcondition check
safety or support escalation detour where relevant
```

Minimum backend test layers:

```text
schema/model tests
service unit tests
repository integration or query tests
API contract tests
auth and owner-scope tests
idempotency replay/conflict tests for retryable writes
audit/outbox tests for side effects
OpenAPI snapshot or generated-client compatibility tests
```

Minimum Agent test layers:

```text
tool contract tests
event/replay contract tests
action lifecycle tests
worker/outbox apply tests
safety guard tests
golden eval or replay case for model behavior
```

### 5. Implement Only Until The Gate Passes

Codex should work until the slice acceptance passes. The implementation should
stay inside the target production backend and must not import legacy runtime
code.

For backend slices, the default verification gate is:

```bash
cd MomCozyAgent
production_backend/.venv/bin/python -m ruff check production_backend/app production_backend/tests
production_backend/.venv/bin/python -m pytest production_backend/tests -q
```

For infrastructure slices, add the relevant live profile check:

```bash
PATH="$HOME/.local/bin:$PATH" docker compose -f production_backend/docker-compose.yml config
production_backend/.venv/bin/python production_backend/scripts/check_redis_runtime_controls.py
production_backend/.venv/bin/python production_backend/scripts/check_backup_restore_hooks.py
```

### 6. Self-Review Before Commit

The self-review checklist is:

- every migrated endpoint is justified by an experience main flow;
- the main flow covers empty, error, retry, and completion states;
- no request body/query `user_id` is an authority;
- no legacy `data_store`, `ChatSession`, or `previous_response_id` dependency;
- error responses use stable envelopes;
- write operations have permission, idempotency, audit, and transaction
  boundaries where required;
- stream events are persisted and replayable;
- unsafe health/emotion/prompt-injection paths cannot continue normal flow;
- tests prove the edge case, not just the happy path.

### 7. Commit The Passing Slice

Commit only after tests and self-review pass. The commit message should name the
behavior, not the implementation detail.

Example:

```text
fix: harden backend idempotency edge cases
feat: add owner-scoped feeding records
feat: persist agent action outbox events
```

## Legacy Surface By Domain

The sections below describe what the old backend exposes and how each area
should be accepted in the production backend.

### Foundation, Assets, And Health

Legacy routes:

- `GET /`, `GET /health`
- `GET /skill-assets/{skill_id}/{asset_path:path}`
- `GET /images/Air_img/{asset_path:path}`
- `POST /api/ag-ui-timing-log`

Business logic:

- serve lightweight service info and static skill/device assets;
- store local JSONL timing logs for AG-UI experiments.

Target acceptance:

- `/v1/health/live` and `/v1/health/ready` have distinct live/ready semantics;
- readiness checks configured infrastructure when enabled;
- static/product assets are served from controlled storage or packaged assets;
- timing/metrics move to structured logs and `/v1/health/metrics`;
- production does not expose local filesystem paths or raw exception messages.

Test gates:

- health live/ready tests;
- metrics auth tests;
- deployment template and runbook checks.

### Auth, User, Mom/Baby Profile, Status, And Analysis

Legacy routes:

- `GET /v1/user/profile/query`
- `GET /v1/mom-baby/info/query`
- `GET /v1/mom-baby/today/query`
- `GET /v1/mom-baby/status-page/query`
- `POST /v1/status/create`
- `POST /v1/analysis/create`

Legacy tables:

- `user_profile`
- `infant_profile`
- `demo_seed_state`

Business logic:

- profile and infant facts are keyed by request-supplied `user_id`;
- status and analysis update profile advice/daily summary;
- status page composes profile, today's schedule, reminders, and summaries.

Target acceptance:

- signup/login/refresh/logout produce backend-derived `CurrentUser`;
- profile and infant APIs derive owner scope from `CurrentUser`;
- profile writes audit actor, request ID, and changed resource;
- infant creation is idempotent and validates ownership;
- status/analysis writes are either deterministic service writes or Agent
  actions, never prompt-only hidden profile mutation;
- cross-user reads and writes fail with stable `owner_scope_violation` or
  `not_found` behavior.

Test gates:

- auth API and dependency tests;
- profile service and API tests;
- owner-scope security tests;
- idempotency replay/conflict tests for retryable creates.

### Files, Media, Vision, And Speech

Legacy routes:

- `POST /v1/files/upload`
- `WEBSOCKET /v1/vision/events/stream`
- `POST /v1/speech/transcribe-chunk`
- `GET /v1/realtime-voice-stream`
- `WEBSOCKET /v1/realtime-voice-session`

Legacy table:

- `uploaded_file`, which lacks owner scope.

Business logic:

- upload stores files on local disk and writes metadata;
- vision stream reads uploaded file by `file_id` and request-supplied `user_id`;
- speech and realtime voice proxy external providers;
- WebSocket auth supports token-in-query patterns.

Target acceptance:

- file metadata has `owner_user_id`;
- object bytes go through object storage abstraction;
- uploads enforce environment-controlled size limits;
- upload/list/detail/delete are owner-scoped;
- deletion queues object cleanup through outbox;
- vision access requires file ownership and emits typed events;
- voice endpoints use bearer auth or a short-lived backend-issued stream token,
  never long-lived access tokens in URLs;
- disabled external providers return stable `503` error envelopes.

Test gates:

- files API/service/model tests;
- object storage tests and integration profile;
- file cleanup outbox handler tests;
- voice API tests for auth, provider disabled, and frame shape;
- no-token-in-URL contract checks for WebSocket/SSE consumers.

### Records: Feeding, Pumping, Growth

Legacy routes:

- `POST /v1/feeding/add`
- `POST /v1/feeding/delete`
- `GET /v1/feeding/query`
- `POST /v1/growth/add`
- `POST /v1/growth/revise`
- `GET /v1/growth/query`
- `GET /v1/growth/history`
- `POST /v1/pump-milk/upload`
- `GET /v1/pump-milk/query`
- `POST /v1/pump-milk/delete`

Legacy tables:

- `feeding_log`
- `pumping_log`
- `infant_growth_log`

Business logic:

- add/query/delete feeding and pumping records;
- map text feed types to internal codes;
- resolve infant for user;
- create, revise, query latest, and query history for infant growth.

Target acceptance:

- records live under owner-scoped `records` module;
- feeding/pumping/growth create/list/delete use typed schemas;
- growth revise is represented by `PATCH /v1/records/growth/{record_id}`
  with partial update semantics;
- infant-linked writes validate infant ownership;
- create endpoints accept `Idempotency-Key`;
- same key plus same request replays, same key plus different body conflicts;
- list endpoints enforce date filters, limits, and stable ordering;
- delete is owner-scoped and idempotency semantics are explicit;
- no plan/summary endpoint uses planned milk as real measured milk.

Current migration status:

- Implemented and tested: feeding/pumping/growth create, list, delete;
  measured-only milk trends; growth partial update; OpenAPI route disposition
  for legacy growth revise.
- Still product-backlog unless re-specified: direct feeding/pumping record
  revise endpoints. Agent-created feeding/pumping writes are represented as
  queued direct-apply actions through explicit tool contracts.

Test gates:

- records service tests for validation, idempotency, and audit;
- records API tests for auth, owner scope, schemas, and filters;
- repository tests for lifecycle filtering and ordering;
- Flutter smoke flow for records after client integration.

### Plans, Calendar, Pregnancy Journey, And Artifacts

Legacy routes:

- `GET /v1/plan/query-task`
- `GET /v1/plan/list`
- `GET /v1/plan/detail`
- `POST /v1/plan/delete-artifact`
- `POST /v1/plan/add-task`
- `POST /v1/plan/delete-task`
- `POST /v1/plan/revise-task`
- `POST /v1/plan/birth-journey/todo-completion`

Legacy tables:

- `calendar`
- `care_plan_artifact`
- `milk_plan`

Agent tools:

- `birth_journey_intake_manage`
- `birth_journey_plan_card_create`
- `birth_journey_plan_delete`
- `birth_journey_plan_todo_update`
- `birth_plan_form_create`
- `labor_communication_card_create`

Business logic:

- query, add, revise, and delete calendar tasks;
- persist care plan artifacts and birth journey plan cards;
- update todo completion for generated 7-day action plans.

Target acceptance:

- deterministic tasks live under `plans` and `plan_tasks`;
- generated content that is part of an Agent run is persisted as
  `agent_artifacts` or promoted to product plans through explicit actions;
- create/update/delete tasks are owner-scoped, audited, and idempotent where
  retryable;
- high-impact Agent plan creation uses proposal/confirmation/apply/audit;
- task completion is a low-risk direct write only after permission and owner
  scope pass;
- birth journey and labor communication flows have golden evals for intent,
  form collection, confirmation, and safety red flags.

Current migration status:

- Implemented and tested: plan/task create, list, detail, delete; task
  completion; `PATCH /v1/plans/tasks/{task_id}` for legacy task revise;
  Agent plan/task proposal contracts for pregnancy and milk scenes.
- Implemented foundation: durable `agent_artifacts` lifecycle and replay
  contract.
- Still missing as product-level flows: birth journey intake/card generation,
  labor communication card generation, hospital bag form/card generation, and
  domain-specific artifact promotion rules. These should be implemented as new
  production flows, not by importing legacy skill/tool code.

Test gates:

- plans service/API/model tests;
- action lifecycle tests for Agent-created plans/artifacts;
- artifact replay tests;
- golden evals for birth prep and birth journey flows.

### Pregnancy Diary And Health Notes

Legacy routes:

- `GET /v1/pregnancy-diary/list`
- `GET /v1/pregnancy-diary/today`
- `POST /v1/pregnancy-diary/create`
- `POST /v1/pregnancy-diary/update`
- `POST /v1/pregnancy-diary/delete`

Legacy tables:

- `pregnancy_diary_entry`
- `pregnancy_diary_health_note`

Agent tool:

- `pregnancy_diary_manage`

Business logic:

- create, update, list, query today, and soft-delete diary entries;
- attach health note content from structured diary interactions.

Target acceptance:

- diary entries are unique per owner and entry date;
- `PUT` upsert restores a soft-deleted row rather than creating a duplicate;
- list/today filters hide deleted entries;
- writes are audited;
- Agent-created diary changes require explicit write policy and idempotency;
- health note content follows medical safety boundaries and does not become a
  diagnosis.

Test gates:

- diary service/API/model tests;
- repository tests for soft-delete/upsert uniqueness;
- Agent diary tool tests;
- health safety evals for diary health notes.

### Pump Device, Telemetry, Workstate, And Pump Process

Legacy routes:

- `POST /v1/device/info`
- `POST /v1/pump/workstate`
- `POST /v1/pump/workstate/pending-replies`
- `POST /v1/pump/process`
- `POST /v1/pump/process/data`
- `WEBSOCKET /v1/pump/session-summary`
- `POST /v1/pump/threshold/upload`
- `GET /v1/pump/threshold/get`
- `GET /v1/pump/energy/get`
- `POST /v1/pump/health/upload`
- `GET /v1/pump/health/get`
- `GET /v1/pump/info/get`

Legacy tables:

- `pump_device_info`
- `pump_workstate_event`
- `pump_workstate_pending_reply`
- `pump_workstate_reply_state`
- `pump_process_point`
- `pump_process_reply_state`
- `pump_threshold`
- `pump_health`

Business logic:

- store pump device info and health/threshold facts;
- ingest workstate/process points;
- generate pending replies and single-session pump summaries;
- run a pump FSM/process calculator.

Target acceptance:

- device metadata and telemetry are owner-scoped under `devices`;
- telemetry ingest is idempotent where client retries are expected;
- pump process/FSM behavior is either migrated as deterministic service logic
  with fixture tests or explicitly retired from the production contract;
- session summaries never expose internal Agent context;
- device safety risks escalate to support/safety paths;
- device state does not live in Agent session state.

Test gates:

- devices service/API/model tests;
- pump-session summary contract tests if the feature is retained;
- deterministic FSM fixture tests for pump process if migrated;
- device-guidance golden evals for safety and model-specific uncertainty.

### Notifications, Schedule Prompts, And Status Page

Legacy routes:

- `GET /v1/notify/query`
- `POST /v1/pump/workstate/pending-replies`
- status-page aggregation routes listed above.

Legacy tables:

- `calendar`
- `pump_workstate_pending_reply`
- `pump_workstate_reply_state`

Business logic:

- query reminders and pending replies;
- merge schedule, pump workstate, and profile status for app display.

Target acceptance:

- notification creation is service-key protected;
- inbox/list/read/archive are owner-scoped;
- schedule/task completion does not create duplicate reminders;
- push or proactive notification support is represented honestly: queued,
  delivered, failed, or unsupported;
- status-page aggregation uses backend services, not raw legacy profile blobs.

Test gates:

- notifications service/API tests;
- service-key auth tests;
- outbox tests for future push delivery;
- Flutter smoke flow for notification read/archive after app integration.

### Support, Hospital Bag Cart, Device Handoff, And Human Support

Legacy routes:

- `POST /api/support-ticket-submit`
- `POST /api/hospital-bag/cart-update`

Agent tools:

- `support_ticket_draft_create`
- `handoff_summary_generate`
- `hospital_bag_form_create`
- `hospital_bag_card_create`
- `hospital_bag_cart_update`
- `hospital_bag_pump_recommend`
- `ibclc_consult_card_create`

Business logic:

- draft or submit support tickets;
- generate handoff summaries;
- create hospital bag forms/cards;
- adjust cart recommendations;
- create IBCLC consultation cards.

Target acceptance:

- support ticket creation is owner-scoped, idempotent, audited, and typed;
- Agent support ticket creation is a confirmable action, then outbox apply;
- cart mutation is either a deterministic low-risk direct write or a
  confirmable action depending on product policy;
- handoff summaries do not leak raw private context;
- IBCLC consultation and device support are separated from medical emergency
  escalation.

Test gates:

- support service/API tests;
- Agent action tests for support ticket proposal/confirm/apply;
- outbox worker tests;
- hospital bag cart tool tests;
- IBCLC and device support golden evals.

### Agent Conversation, Skill Runtime, Tools, And Streaming

Legacy routes:

- `POST /api/ag-ui`
- `WEBSOCKET /api/ag-ui-ws`
- `POST /api/ag-ui-prewarm`
- `POST /api/ag-ui-cancel`
- `POST /api/client-event`

Legacy runtime state:

- in-memory `ChatSession`;
- provider `previous_response_id`;
- loaded skill IDs;
- mixed `ContextState`;
- process-local run/cancel state.

Legacy tools:

- skill runtime: `list_skills`, `load_skill`, `search_skill_assets`,
  `read_skill_file`, `run_approved_skill_script`;
- profile: `profile_get`, `profile_update`;
- birth prep and hospital bag tools;
- milk management read/write tools;
- device/support tools;
- diary, IBCLC, handoff, quick replies.

Target acceptance:

- production runtime uses LangGraph + OpenAI Agents SDK;
- no legacy Responses API loop, no `previous_response_id`, no old adapter
  fallback;
- threads, runs, messages, tool calls, tool outputs, actions, artifacts,
  events, checkpoints, safety events, and eval cases are persisted;
- Redis stores only reconstructable controls: active run lock, cancel flag,
  stream cursor, and short-lived cache;
- event stream uses application event envelopes with `event_id`, `thread_id`,
  `run_id`, `sequence`, `type`, `payload`, and `created_at`;
- reconnect/replay works from `after_sequence`;
- action confirmation/rejection enforces status, expiry, owner scope, and
  idempotency;
- non-blocking side effects use outbox and return queued/applied/failed events;
- context projection is rebuilt per turn from message ledger, business facts,
  memory, and current state projection.

Current migration status:

- Implemented and tested: scene specialist routing, durable run/message/tool
  ledger, context projection, SDK tool execution, action proposal/outbox
  skeleton, replayable application events, deterministic safety guard, and
  product eval seed runner.
- Implemented lactation foundation: `records.milk_summary.read`,
  `records.milk_status.read`, feeding/pumping record direct-apply proposals,
  milk plan proposal, and milk reminder proposal.
- Still missing as product-level flows: full milk analysis card, calendar
  reschedule previews, day-by-day milk plan calendar mutation, IBCLC consult
  workflow, and richer birth-prep artifacts. Add eval cases and tool/action
  contracts before implementation.

Test gates:

- Agent runtime API tests;
- event and streaming replay tests;
- action lifecycle and outbox apply tests;
- tool executor contract tests;
- graph/checkpoint tests;
- worker lock/cancel/recovery tests;
- replay bundle and eval seed tests;
- golden evals for tool choice, safety, confirmation, and domain flows.

## Product-Level Agent Acceptance Suites

Use `production_backend/docs/product/momcozy-agent-service-test-plan.md` as the product behavior source.
Each suite should become a deterministic eval set with mock tools and safe
fixtures.

Required suites:

```text
birth_prep
labor_communication
device_guidance
device_support_handoff
milk_daily_summary
pump_session_summary
milk_schedule_management
milk_plan_creation
milk_trend_analysis
ibclc_consult
health_consultation
emotion_support
mixed_intent_and_safety
permission_bypass
prompt_injection
```

Acceptance for each suite:

- correct intent/service selection;
- minimal clarification when required facts are missing;
- no write/apply/submit/delete without confirmation when required;
- correct tool/action choice;
- high-risk health or emotional safety cases interrupt normal flow;
- final response avoids diagnosis, medication, promises, and hidden internal
  state;
- stream events contain expected run/tool/action/message transitions.

## PR Slice Template

Use this template when starting a new Codex loop.

```text
Slice:
Experience main flow:
Legacy source:
Target module:
Target endpoint/action/tool:
Target schema:
Owner scope:
Write policy:
Idempotency:
Audit:
Outbox/worker:
Safety/eval:
Tests to add before implementation:
Experience-flow acceptance:
Verification command:
Definition of done:
```

## Definition Of Done For A Migrated Domain

A domain is accepted only when all of these are true:

- target APIs are typed and represented in OpenAPI;
- each public API or Agent action is covered by an experience main flow;
- migrated routes derive owner scope from `CurrentUser`;
- old response shapes are not required by new clients;
- business writes have audit and idempotency where appropriate;
- cross-user access tests exist;
- legacy `data_store` and local SQLite are not referenced;
- migrations cover tables and indexes;
- relevant worker/outbox behavior is tested;
- relevant Agent tools/actions/evals are tested;
- release smoke flow or Flutter smoke fixture exists when the app consumes it;
- docs identify any intentionally retired legacy behavior.

## Current Priority Backlog

The backend already has a strong production foundation. The next engineering
loop should focus on gaps where legacy behavior has not yet been fully promoted
to a production contract:

1. Decide which pump process/FSM and pump workstate features are retained,
   retired, or moved behind deterministic service fixtures.
2. Convert the 14 product Agent service suites into machine-readable eval
   cases with mock business fixtures.
3. Add per-domain PR slice records for any legacy route not yet represented by
   the new OpenAPI surface or intentionally retired; each record should start
   from the feature's experience main flow.
4. Add Flutter generated-client gates once the Flutter refactor owns codegen.
5. Promote production incidents or manual QA failures into replay/eval
   regression cases.
