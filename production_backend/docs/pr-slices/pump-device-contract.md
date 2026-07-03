# Pump Device Contract Slice

Status: accepted production contract

## Experience Main Flow

Device-related app screens need to send pump state and facts, then read the
latest user-visible device facts without involving Agent session state.

The retained production flow is:

```text
app/device sends pump device metadata
-> backend derives owner from CurrentUser
-> device metadata is upserted under owner scope
-> app/device sends telemetry, workstate, threshold, or health facts with an
   Idempotency-Key when retries are expected
-> backend persists each fact as owner-scoped telemetry
-> app reads latest projections for workstate, threshold, and health
-> app reads static pump energy target
```

## Retained Production Endpoints

- `PUT /v1/devices/pumps/{device_id}`
- `GET /v1/devices/pumps`
- `POST /v1/devices/pump-telemetry`
- `GET /v1/devices/pump-telemetry`
- `POST /v1/devices/pump-workstate`
- `GET /v1/devices/pump-workstate/latest`
- `POST /v1/devices/pump-threshold`
- `GET /v1/devices/pump-threshold/latest`
- `POST /v1/devices/pump-health`
- `GET /v1/devices/pump-health/latest`
- `GET /v1/devices/pump-energy-target`

## Retired Legacy Endpoints

These legacy routes are not part of the production contract and must not be
kept as fallback paths:

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

The old pump process/FSM and pending-reply behavior is retired until the product
team re-specifies it as deterministic service logic with fixture tests. The old
`pump_info` summary was a records/analytics concern, not a device metadata
contract, so it must be rebuilt under records or analytics if retained.

## Acceptance

- no request body or query `user_id` is authoritative;
- writes are owner-scoped and audited;
- retryable writes declare `Idempotency-Key`;
- latest reads are owner-scoped projections, not Agent session state;
- OpenAPI contains only the production `/v1/devices/...` routes;
- OpenAPI regression tests reject legacy `/v1/pump/...` fallback paths.
