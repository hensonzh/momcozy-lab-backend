# Backend Refactor Inventory

Status: draft

This inventory freezes the legacy backend surface before behavior is moved into
the production backend.

## API Surface

To be filled from:

- `legacy_backend/src/momcozy_agent/api/routes.py`
- `legacy_backend/src/momcozy_agent/api/chat_ws_bridge.py`
- `legacy_backend/src/momcozy_agent/api/vision_stream.py`
- `legacy_backend/src/momcozy_agent/server.py`

For each endpoint, record:

```text
method / route
request schema
response schema
auth requirement
current user source
owner scope
side effects
idempotency
audit requirement
test coverage
```

## SQLite Schema

To be filled from `legacy_backend/src/momcozy_agent/services/data_store.py`.

Record:

```text
table
columns
indexes
owner scope
write paths
future PostgreSQL module
```

## Agent Runtime Surface

To be filled from:

- `legacy_backend/src/momcozy_agent/agents.py`
- `legacy_backend/src/momcozy_agent/server.py`
- `legacy_backend/src/momcozy_agent/contexts.py`
- `legacy_backend/src/momcozy_agent/tool_registry.py`
- `legacy_backend/src/momcozy_agent/tool_schemas.py`

Record:

```text
run/session state
tool name
read/write classification
side effect level
safe args/result policy
stream events
frontend reducer dependency
```

## Migration Guardrails

- New production code must not depend on legacy `ChatSession`.
- New production code must not use `previous_response_id`.
- New production code must not treat SQLite as authority.
- Temporary bridges must be isolated under `production_backend/legacy_bridge/`.
