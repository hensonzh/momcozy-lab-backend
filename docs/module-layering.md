# Module Layering

Production backend modules should keep protocol handling, use-case orchestration,
domain rules, and persistence separate enough that each layer can be tested
without pulling in unrelated dependencies.

## Recommended Files

```text
router.py
  HTTP/WebSocket protocol adapter, dependency injection, current_user, request
  schema, response schema.

service.py
  Application service / use-case orchestration. Owns permission checks,
  owner-scope checks, idempotency, transaction coordination, audit, and
  repository/client calls.

domain.py
  Pure domain rules and calculations. Use it for validation predicates,
  state-transition decisions, date windows, scoring, trend calculation,
  summaries, and business invariants that should not depend on FastAPI, DB
  sessions, repositories, Redis, object storage, audit, or idempotency services.

repository.py
  Database reads/writes only. No HTTP decisions, no request/user parsing, no
  product workflow branching.
```

## When To Add `domain.py`

Add `domain.py` when a module has rules that are:

- reused by public and internal application services;
- complex enough to deserve direct unit tests;
- stable business rules rather than persistence mechanics;
- calculations over records, state transitions, or payload normalization.

For small CRUD-only modules, `domain.py` can wait until rules emerge.

## Agent Runtime Integration Boundary

Agent Runtime is an external service. Product Backend business modules may expose a
small internal adapter next to the owning module:

```text
agent_router.py
  /v1/internal/agent/* HTTP adapter and Runtime service authentication.

agent_contracts.py
  Bounded Product Backend request/response schemas for the internal service API.

agent_service.py
  Owner-scope, action-bound idempotency, audit, and business-service reuse.
```

These files are Product integration adapters, not an Agent loop. They must not
contain prompts, tool routing, model calls, conversation state, confirmation
workflow, memory, or Runtime recovery logic. They call the same application
services used by Product routes and preserve the same domain invariants.

The Product repository must not import Agent Runtime packages or access its
database. The Runtime must not import Product modules or access Product tables;
all cross-service access goes through the authenticated internal HTTP contract.

Read adapters receive explicit `actor_user_id` and enforce owner scope. Action
adapters require `X-Service-Key`, `action_id`, and
`Idempotency-Key: agent-action:<action_id>`, then record the Runtime service as
the audited actor service.

## Current Example

`records/domain.py` owns milk-trend windows and measured trend calculation,
required measurement predicates, list/trend limit predicates, and growth update
field decisions.

`records/service.py` still owns owner scope, idempotency, audit, replay, and
repository calls. This is the desired boundary:

```text
domain.py
  "Is this measurement valid?"
  "What are the trend days?"

service.py
  "Can this user perform this operation?"
  "Should this idempotency key replay or reserve?"
  "Which repository method and audit action should run?"
```
