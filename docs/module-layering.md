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

- reused by service, agent action handlers, evals, or background workers;
- complex enough to deserve direct unit tests;
- stable business rules rather than persistence mechanics;
- calculations over records, state transitions, or payload normalization.

For small CRUD-only modules, `domain.py` can wait until rules emerge.

## Agent Integration Boundary

Business modules expose models, domain rules, repositories, and application services. Agent-specific Action handlers and Tool handlers live under `app/agents/cozymate/` and call those services; business modules do not import `app.agent_runtime`, `app.agents`, or `app.workers`. Shared execution contracts belong to `app/agent_runtime/`, and background process mechanics belong to `app/workers/`.

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
