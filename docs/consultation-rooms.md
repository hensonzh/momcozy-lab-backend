# Consultation rooms

Mom and IBCLC clients share `/v1/care/appointments/{appointment_id}/room` and the same Product Backend session authentication. The server derives the participant role from appointment ownership and the active assigned provider; requests cannot choose an actor or role.

The Mom client confirms her current region and grants video consent separately from case-sharing consent. Entry also requires a submitted intake. A confirmed appointment opens ten minutes before its scheduled start and closes fifteen minutes after its scheduled end; a consultation that the expert has already started permits re-entry. Consent is checked again whenever credentials are issued and during presence updates. Withdrawing either video or case-sharing consent queues room closure. Explicitly restoring consent creates a new room generation, so credentials for the old room cannot connect to the new consultation room.

Only the assigned IBCLC can start or end a consultation. Starting requires both participants to be present; LiveKit presence is verified against its server API. The current intake revision is pinned at start. A completed consultation consumes one entitlement in the same transaction as the outcome, protected by a unique consumption record. Technical failure, no-show and safety escalation consume none. No-show requires at least ten minutes past the start and no prior user attendance. User leave, media failure and room deletion never implicitly end a consultation.

## Configuration and worker

`CONSULTATION_VIDEO_PROVIDER` defaults to `disabled`. Use `sandbox` for authenticated two-client workflow tests without media, or `livekit` with `CONSULTATION_LIVEKIT_URL`, `CONSULTATION_LIVEKIT_CLIENT_URL`, `CONSULTATION_LIVEKIT_API_KEY` and `CONSULTATION_LIVEKIT_API_SECRET`. The server URL is used for LiveKit room-service calls; the client URL is returned in short-lived join credentials and must be reachable by both clients. Production requires WSS for both URLs and rejects sandbox video and `CONSULTATION_DEMO_EARLY_JOIN=true`.

Apply migrations, then run the API and the durable room worker with the same database and video configuration:

```bash
python -m alembic upgrade head
python -m app.workers.care_video
```

For local Compose, set the private environment file and enable the `consultations` profile:

```bash
docker compose -f docker-compose.local.yml --profile consultations up -d api care-video-worker
```

The worker claims due `care_video_commands` with `FOR UPDATE SKIP LOCKED`. Creation and closure are idempotent provider calls. A failure retains the command with bounded backoff; a process interruption rolls back the claim so another process retries it. Cancelled or superseded creation commands cannot reopen a room. Persisted errors contain an exception class only. Monitor pending command age, retries and worker availability; an unavailable worker leaves the UI in a preparation or closure state.

LiveKit join credentials expire after sixty seconds, permit camera/microphone publication and subscription only, and are returned with `Cache-Control: private, no-store`. They are not persisted. Room closure disconnects existing participants; credentials for self-hosted LiveKit are not assumed to support immediate token revocation, so consent restoration always uses a fresh room name.

## Verification

`tests/test_care_rooms.py` uses isolated PostgreSQL schemas to exercise concurrent completion, exact entitlement consumption, leave/rejoin, stale connections, consent withdrawal/restoration, worker retries and cancellation. Set `MOMCOZY_TEST_DATABASE_URL` to a dedicated local test database. The SQLite fallback is not used for lock/concurrency assertions.

```bash
python -m pytest -q tests/test_care_booking.py tests/test_care_intake.py tests/test_care_rooms.py tests/test_care_video_provider.py
```

The Flutter room lives in `modules/consultation`, with shared domain models in `domain/care` and the media adapter in `services/consultations`. `flutter test test/modules/consultation` verifies preparation and waiting layouts at 320/390/430, narrow-screen large text, connection recovery, leave confirmation and parent rebuilds. Sandbox fixtures make no claim about actual remote audio or video quality.

For actual media, install `requirements-rtc-test.txt`, start an isolated LiveKit server and set `MOMCOZY_TEST_LIVEKIT_CONFIG` to a private JSON file containing `server_url`, `api_key` and `api_secret`. Run `python -m pytest -q tests/test_care_livekit_integration.py` with the dedicated PostgreSQL URL. This opt-in test publishes synthetic camera frames and audio in both directions, verifies server presence before the expert starts, then verifies durable room deletion disconnects both clients and consumes exactly one consultation. On 2026-09-08 it passed against locally compiled LiveKit `v1.13.6` and the Python RTC client `1.1.18`; it does not test physical cameras, mobile device permissions or external-network quality.

Provider references: [LiveKit Flutter SDK](https://pub.dev/packages/livekit_client), [room service API](https://docs.livekit.io/reference/other/roomservice-api/), [tokens and grants](https://docs.livekit.io/frontends/reference/tokens-grants/).
