# Redis Runtime Profile

The default local test suite uses fakes for agent runtime controls. Production
readiness still needs a live Redis profile for reconstructable transient state.

## Runtime Boundary

Redis is allowed to store:

- `agent:run:{run_id}:lock`
- `agent:run:{run_id}:cancel`
- `agent:run:{run_id}:stream_cursor`
- `agent:run:{run_id}:transient_stream`
- `agent:thread:{thread_id}:active_run`

Redis must not be the authority for business state, messages, actions, audit
logs, or artifacts. If Redis is flushed, Postgres still explains the durable run
ledger and the service can recover or fail runs explicitly.

`agent:run:{run_id}:transient_stream` is a short-TTL Redis Stream for live
`message.delta` typing events. It is an experience layer only: deltas are not
persisted to Postgres, do not carry the database `sequence`, and can be replayed
or dropped without changing the durable run result. The persisted
assistant `message.completed` event, including `payload.text`, and message
ledger remain authoritative.

## CI Profile

The `redis-runtime-controls` CI job starts a Redis service and runs:

```bash
REDIS_URL=redis://localhost:6379/0 \
python production_backend/scripts/check_redis_runtime_controls.py
```

The script pings Redis, verifies active-run, cancel, stream cursor, transient
stream, and exclusive-lock semantics, and cleans up the generated keys.

## Local Profile

When Docker is available:

```bash
docker compose -f production_backend/docker-compose.yml up -d redis
REDIS_URL=redis://localhost:6379/0 \
  python production_backend/scripts/check_redis_runtime_controls.py
```

This live profile is separate from the fast unit/contract suite so normal
development does not require local infrastructure.
