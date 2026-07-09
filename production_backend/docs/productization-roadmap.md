# MomCozy Production Backend Productization Roadmap

Status: source-of-truth execution roadmap for backend-only productization before
Flutter integration.

This document controls the current backend work loop:

```text
Phase 0 -> Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 -> Phase 5 -> Phase 6
```

Flutter integration is intentionally excluded from this loop. The backend must
finish its own service-side contracts first, then hand a stable API/event
surface to the Flutter refactor.

## Scope Boundary

In scope before Flutter integration:

- backend API contracts, error envelopes, OpenAPI snapshot, and idempotency;
- PostgreSQL, Redis, object storage, migrations, settings, CI, and runbooks;
- owner-scoped product modules and their experience-flow tests;
- outbox worker and agent worker runtime controls;
- production agent runtime, service skill routing, tools, actions, streaming, replay,
  and eval;
- safety, privacy, observability, and release gates.

Out of scope before Flutter integration:

- generated Flutter client CI job;
- Flutter reducer implementation;
- App-side memory management UX;
- native mobile permission, notification, BLE, and speech UX;
- final wording and visual treatment of safety/handoff screens.

The backend may document contracts for these out-of-scope areas. It must not add
temporary compatibility code for the legacy app.
The backend must not add temporary compatibility code for the legacy app.

## Phase Status Legend

- `green`: code-local acceptance is complete and covered by tests/docs.
- `yellow`: backend foundation exists, but production readiness still needs a
  real environment, provider credential, content decision, or staging drill.
- `red`: required backend behavior is missing.

## Phase 0: Productization Baseline And Acceptance Matrix

Goal: make the productization loop executable instead of relying on memory or
ad-hoc review.

Deliverables:

- one roadmap that maps productization phases to docs, tests, and PR slices;
- explicit backend-vs-Flutter boundary;
- status matrix for retained product domains and agent scenarios;
- completion criteria for backend-only productization;
- self-review gate before every commit.

Canonical references:

- `backend-refactor-execution-plan.md`
- `legacy-backend-acceptance-loop.md`
- `agent-runtime-continuation-migration-plan.md`
- `api-contract-handoff.md`
- `deployment-runbook.md`
- `release-smoke-checklist.md`

Acceptance:

- every retained domain has a phase owner and an acceptance surface;
- every `yellow` item has a concrete next PR or external dependency;
- no Phase 0-6 item is blocked by Flutter implementation details;
- tests verify this roadmap remains present and wired into docs.

Current status: `green` after this document and its doc tests land.

## Phase 1: Backend Infrastructure And Environment Hardening

Goal: make the isolated FastAPI backend start, validate configuration, migrate,
and expose health in local/test/staging/production profiles.

Acceptance:

- local development can use Docker Compose Postgres, Redis, and MinIO;
- staging/production can switch to managed Postgres, Redis, and OSS/S3-compatible
  storage through environment variables;
- production startup rejects implicit localhost DB/Redis and unsupported local
  object storage;
- `/v1/health/ready` depends on real infrastructure checks;
- CI runs lint, type check, tests, migrations, OpenAPI drift, compose validation,
  image build, live Postgres migration, live Redis controls, and object storage
  profile checks;
- deployment runbook covers backup/restore, credential rotation, worker backlog,
  agent recovery, rollback, and security incident response.

Current status: `green` for code-local acceptance. Real staging/production
credential checks remain external.

Already covered:

- typed settings and profile examples;
- Docker/compose services and optional worker profile;
- live Postgres, Redis, and object storage CI jobs;
- MinIO-backed local and managed object storage abstraction;
- readiness checks and deployment docs.

Completed backend-only PR slices:

- `ops: add staging smoke command bundle`
- `ops: add production readiness checklist status command`

External dependencies:

- actual staging/production managed Postgres, Redis, and OSS credentials.

## Phase 2: Core Product Business Modules

Goal: complete deterministic product APIs before depending on agent or app
integration.

Retained domains:

| Domain | Backend surface | Acceptance surface | Status |
| --- | --- | --- | --- |
| Auth/session | signup, login, refresh, logout, service key | auth API and active-session tests | green |
| Profiles | user profile, infant profile | owner scope, idempotent infant create | green |
| Files/assets | upload, metadata, soft delete, product asset manifest | owner scope, object storage, outbox cleanup | green |
| Records | feeding, pumping, growth | experience flows, domain rules, idempotency | green |
| Plans/tasks | plans, tasks, complete/delete/update | experience flows and audit | green |
| Pregnancy diary | list/get/upsert/delete by date | soft-delete restore and audit | green |
| Devices | pump metadata, telemetry, retained reminders | owner-scoped APIs and experience tests | green |
| Notifications | task reminder and milk-analysis related flows | service-key create, inbox/read/archive | green |
| Support | support tickets and agent handoff target | owner scope, idempotency, audit | green |
| Status page | operational status | public/service surface tests | green |
| Voice | provider-neutral disabled/local_stub contract | standard disabled-provider envelope | green |
| Vision | provider-neutral disabled/local_stub contract and file-owner stream contract | standard disabled-provider envelope | green |

Current status: `green` for code-local acceptance. Managed speech and vision
providers remain external launch-scope choices.

Acceptance:

- no migrated route trusts request body/query `user_id` as an authority;
- no migrated route imports legacy runtime or `data_store`;
- retained write routes have permission, transaction, idempotency where retryable,
  and audit where required;
- each retained experience main flow has tests for success, empty/error, owner
  scope, retry/idempotency, and persisted postconditions.

Completed backend-only PR slices:

- `feat: add provider-neutral speech client boundary` if voice becomes a launch
  requirement;
- `feat: add provider-neutral vision client boundary` if image analysis becomes a
  launch requirement.

External dependencies:

- managed speech/vision provider choice and product launch scope.

## Phase 3: Worker, Outbox, And Async Effect Lane

Goal: keep API request paths fast while making side effects durable, observable,
and retryable.

Acceptance:

- outbox worker can process registered jobs independently of API workers;
- agent worker can process queued/stale runs independently of API workers;
- Redis locks prevent duplicate run/job execution across worker replicas;
- retries, dead-letter status, lease timeout, and graceful shutdown are tested;
- non-blocking agent effects use action/outbox and emit replayable events.

Current status: `green` for code-local acceptance. Staging alert thresholds
remain external.

Already covered:

- `scripts/run_agent_worker.py` and `scripts/run_outbox_worker.py`;
- compose `workers` profile;
- file cleanup and agent action outbox handlers;
- worker lifecycle, retry, DLQ, and process tests.

Completed backend-only PR slices:

- `ops: add worker backlog inspection CLI`
- `ops: add stuck-run recovery CLI`
- `test: add worker operations smoke fixtures`

External dependencies:

- staging worker deployment shape and operational alert thresholds.

## Phase 4: Production Agent Runtime

Goal: replace the legacy Responses API loop with durable LangGraph + OpenAI
Agents SDK runtime and product-scenario service skills.

Fixed decisions:

- use LangGraph + OpenAI Agents SDK;
- do not keep the old self-written loop, `previous_response_id`, `ChatSession`,
  AG-UI bridge, or legacy adapter fallback;
- backend owns message history arrays, runtime ledger, tool/action contracts, and
  application events;
- service skill routing is scenario-based: pregnancy service, lactation,
  postpartum recovery, after-sales, safety guardrail, and general assistant.

Acceptance:

- thread, run, message, event, tool call, action, artifact, checkpoint, safety,
  memory, and eval ledger rows are durable;
- every exposed tool has contract metadata, input schema, permission, owner scope,
  side-effect level, blocking policy, timeout, and safe result behavior;
- natural-language requests route through deterministic checks and service skill
  routing without overbuilding a separate platform;
- streaming uses application events with sequence replay and optional transient
  `message.delta`;
- model/provider state never replaces the internal ledger;
- product eval covers retained agent scenarios.

Current status: `green` for code-local acceptance. Real provider credentials,
budget owner approval, and content review remain external.

Already covered:

- durable runtime ledger and Redis controls;
- executable graph runner boundary and SDK runner boundary;
- service skill plans and tool allowlists;
- action policy, outbox application, replay bundles, memory, safety, and eval
  harness;
- product seed eval suites for milk, pregnancy, diary, device, support, safety,
  prompt injection, permission, and memory.

Completed backend-only PR slices:

- `agent: add postpartum recovery service skill fixtures and eval cases`;
- `agent: defer provider-backed eval until product need is proven`.

External dependencies:

- real-provider failure telemetry tuning after credentials and staging runs
  exist.

External dependencies:

- `OPENAI_API_KEY`, launch model choice, provider eval budget, and product-owned
  prompt/content review.

## Phase 5: Safety, Privacy, Permission, And Cost Controls

Goal: make high-risk mother-and-baby scenarios safe before real users interact
with the system.

Acceptance:

- deterministic safety gates cover maternal health, infant health, emotional
  crisis, possible harm to baby, prompt injection, and permission bypass;
- unsafe input cannot continue normal business flows or create business actions;
- safety decisions are persisted, replayable, and countable without exposing PII
  in metrics;
- action responses and stream events never expose server-only `apply_payload`;
- auth/session/owner-scope tests prove cross-user access fails closed;
- provider-backed evals are deferred; deterministic seed and replay evals remain
  the current gate.

Current status: `green` for code-local acceptance. Product-approved safety copy
and real provider budget approval remain external.

Already covered:

- safety event schema and deterministic guard;
- safety eval seed suites and forbidden side-effect assertions;
- PII-conscious metrics for safety and runtime outcomes;
- action preview/apply payload separation;
- service-key boundary and owner-scope tests.

Completed backend-only PR slices:

- `security: add PII redaction regression fixtures for replay exports`;
- `security: add provider cost budget env contract`;
- `security: add launch safety template registry placeholder`.

External dependencies:

- legal/product-approved safety copy and real provider budget.

## Phase 6: Observability, Test Gates, And Release Readiness

Goal: make backend quality measurable and repeatable before Flutter integration.

Acceptance:

- CI covers ruff, mypy, pytest, migrations, OpenAPI snapshot, legacy-reference
  scan, compose validation, image build, Redis, Postgres, object storage, and
  seed eval smoke;
- release smoke covers auth, files, records/plans, agent stream/replay, metrics,
  workers, rollback, backup/restore, and security incident response;
- provider-backed eval workflow is not part of the current gate;
- production incidents can become redacted replay bundles and regression eval
  cases;
- every Phase 0-6 yellow item has either a backend PR or an external dependency.

Current status: `green` for code-local acceptance. Staging dashboard and alert
destinations remain external.

Already covered:

- production backend CI workflow;
- agent seed eval report artifacts;
- provider eval workflow entry;
- deployment runbook and smoke checklist;
- replay bundle and eval seed services.

Completed backend-only PR slices:

- `ops: add productization status checker`;
- `eval: add provider budget and max-case defaults to nightly workflow`;
- `obs: add runbook links to metrics and worker backlog docs`.

External dependencies:

- staging deployment, dashboard destination, alerting destination, and real
  provider credentials.

## Backend-Only Completion Criteria Before Flutter Integration

Phase 0-6 are complete when all of the following are true:

- every phase is `green`, or its only remaining item is explicitly external and
  documented above;
- all backend-local PR slices in this roadmap are implemented or intentionally
  retired with a documented reason;
- `python -m ruff check app tests scripts` passes inside `production_backend/`;
- `python -m mypy app scripts` passes inside `production_backend/`;
- `python -m pytest production_backend/tests` passes from the repository root;
- `python production_backend/scripts/run_agent_seed_eval.py` passes and writes a
  report;
- OpenAPI export has no drift from `docs/openapi.generated.json`;
- CI and release docs still state that Flutter integration starts only after the
  backend contract is stable.

## Execution Order From Here

1. Finish Phase 0 by landing this roadmap and doc tests.
2. Turn Phase 1 `yellow` into `green` for all code-local items by adding smoke
   command/status tooling.
3. Turn Phase 3 `yellow` into `green` for all code-local items by adding worker
   inspection/recovery tooling.
4. Turn Phase 4 `yellow` into `green` for code-local items by expanding
   postpartum and provider-budget eval coverage.
5. Turn Phase 5 `yellow` into `green` for code-local items by adding redaction,
   cost, and safety-template checks.
6. Turn Phase 6 `yellow` into `green` for code-local items by wiring the new
   status/budget checks into tests and docs.
7. Stop before Flutter integration and hand over `api-contract-handoff.md`,
   `openapi.generated.json`, and `flutter-smoke-flows.json`.
