# Stool photo review implementation status (2026-10-22)

**Not a production-ready medical service.** This slice provides only dedicated private photo upload and a fail-closed review queue. There is no medically validated analysis endpoint, completed result, field-level card stream, or calibrated admission threshold; Product only supports a nonmedical shell/failure replay. The App now uses the dedicated picker/repository behind a fail-closed capability check. New legacy poop-marked Agent Runs are rejected by Agent Runtime. Do not enable/release it as the reviewed service.

## Endpoints and worker

- `GET /v1/stool-photos/capability`: bearer; both `review_available` and `analysis_available` are false until their independent release gates pass. App disables capture/upload while review is unavailable; the Backend now also rejects even authenticated direct uploads with `503 / stool_photo_review_unavailable` before storing an original. The capability and upload route share the same fail-closed gate, which is false until the service is separately released.
- `POST /v1/stool-photos`: currently gated off; when review is released: bearer, `Idempotency-Key`, multipart original image `file`; body bytes capped at 10 MiB. Detect actual JPEG/PNG/HEIC encoding and decode, short edge at least 720 px after orientation, max 64 million decoded pixels. Ignores client-provided filename/MIME as format evidence. Creates a private `stool_photos` row and returns `202`/`pending` with `photo_id` and `review_id` (same ID).
- `GET /v1/stool-photos/{photo_id}/review`: owner-scoped status and stable reason codes. Never returns raw image, provider scores, EXIF or storage key.
- `DELETE /v1/stool-photos/{photo_id}`: owner-scoped immediate tombstone; durable worker retries object removal. This does **not** guarantee the deletion can recall an image already sent to a third party.
- Start `python -m app.workers.stool_photo_reviews` **only after applying Alembic revision `20261020_0027`** and configuring object storage. It claims `pending` or expired review leases, verifies the original SHA-256, strips EXIF in a derived JPEG, and records `passed/rejected/inconclusive/failed`. A disabled provider records `failed`; it does not approve. The same worker now purges only originals explicitly tombstoned by the user or account deletion; it does **not** age-purge originals. A passed review is usable for submission for 180 days from `reviewed_at` (exclusive expiry boundary). This product-selected window is not a clinical validity claim or an approved privacy retention policy. Expired reviews cannot authorize a new submission; the user must obtain a new review, currently by deleting/re-uploading. Existing one-day approvals are not silently extended in the database; they must be reviewed again. Originals remaining after review expiry still require an approved retention/deletion policy and orphan-object reconciliation before release. The worker is wired only into local Compose and the CI smoke stack, after migration. Production/UAT deployment Compose is unchanged. Without a running worker in a future approved environment, `pending` will remain pending.

`STOOL_PHOTO_REVIEW_PROVIDER=disabled` by default. `decisions` requires `STOOL_PHOTO_THIRD_PARTY_APPROVED=true` and `OPENAI_API_KEY`; the environment flag represents an **external privacy approval** and cannot itself provide one. Even enabled, the Decisions candidate returns `inconclusive` until the pediatric image policy, thresholds and consented evaluation set are approved. The adapter never produces a medical conclusion. Don't enable third-party transfer as a debugging shortcut.

The server-side admission helper `require_approved_photos` checks count, uniqueness, owner, deletion/purge, current status, the 180-day review expiration and the binding to the original SHA-256. The medical submission endpoint is **not implemented**. A synthetic-only in-process submission slice now re-reads and verifies the private original, but it is not exposed to App or dispatched to medical analysis. App picks unscaled bytes, checks size/format and locally decodable short edge (HEIC that cannot be decoded locally is validated by Backend), uploads behind a server capability check, polls per-photo status, and keeps analysis disabled until the separate specialty service exists. Uploads lock an active account row so concurrent account deletion cannot leave a new eligible photo; account deletion revokes review eligibility and tombstones unpurged photos in the same database transaction; physical object deletion is eventual via the retrying worker, **not immediate**. Request commit failures and partial object writes trigger best-effort rollback cleanup. An abrupt process death between object write and database commit (or a failed compensation delete) can still strand an untracked private original; a durable reservation/object reconciliation process and incident response/medical-record retention policy are release blockers.

## B US-East UAT manifest (not deployed by this change)

The B-only Compose manifest now includes `stool-photo-review-worker` in Product's
first activation, update/recovery and image-provenance checks. Its environment
**forces** `STOOL_PHOTO_REVIEW_PROVIDER=disabled` and
`STOOL_PHOTO_THIRD_PARTY_APPROVED=false` regardless of the private env. A
pending photo is failed without reading its original; the worker may still
retry deletion tombstones. This code/manifest check is not evidence that the
worker is running on the host. Verify actual B release provenance, migration,
process health and a synthetic deletion rehearsal before any user-facing
capability can be enabled. A-lane manifests remain unchanged.

## Local and CI worker verification

`make backend-up` now builds/starts `stool-photo-review-worker` **after** the migration; the local Compose and CI override force `STOOL_PHOTO_REVIEW_PROVIDER=disabled` and `STOOL_PHOTO_THIRD_PARTY_APPROVED=false` regardless of the local env file. The CI smoke job starts the worker after Alembic, verifies the process is running, then executes the PostgreSQL + MinIO integration tests using fresh random schema and bucket; cleanup removes only those test resources. Do **not** use an existing user bucket for these tests. No real pediatric photos, third-party review or production deployment is involved.

For manual isolated validation against the existing *local* infrastructure (creates/drops only randomized schema and bucket):

```bash
MOMCOZY_TEST_DATABASE_URL=postgresql+asyncpg://momcozy:momcozy@localhost:5432/momcozy \
MOMCOZY_TEST_OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000 \
python -m pytest -q tests/test_stool_photo_postgres_integration.py
```

Tests use generated synthetic images. An object-storage outage keeps the tombstone for retry; a failed rollback cleanup or a process crash before commit requires a future durable orphan reconciliation path. Do not enable an admission capability based only on this worker running: the provider is disabled and medical analysis is not implemented.

## Local checks

`python -m pytest -q tests/test_stool_photo_*.py tests/test_openapi_contract.py tests/test_migration_smoke.py`

`python -m ruff check app/modules/stool_photos app/workers/stool_photo_reviews.py tests/test_stool_photo_*.py`

`python -m mypy app/modules/stool_photos app/workers/stool_photo_reviews.py`

No real pediatric photos or external API calls belong in this test suite. SQLite tests cover core behavior; the isolated PostgreSQL + MinIO suite additionally covers owner isolation, a second worker claim, expired-lease takeover, stale result prevention, account revocation and deletion retry. These tests do **not** constitute pediatric review-policy calibration, third-party privacy approval, orphan-object reconciliation, or a medical specialty service.

## Follow-up context implementation slice (2026-10-22)

`app/modules/stool_analyses/` now contains a durable result/photo-link ledger (migration `20261022_0028`), an in-process resolver, and a dedicated Agent-only internal POST endpoint. The endpoint requires the Agent Runtime service identity and **always returns unavailable by default**; tests alone override that gate. With synthetic seeded results it checks current account/device session, baby/thread/owner, result/photo state, SHA-256, and retention on every request, then sends only EXIF-stripped in-memory JPEGs and a bounded result. Account deletion revokes access and scrubs the persisted result in the same DB transaction. The Agent typed client and transient model-input adapter are wired to a versioned analysis reference but remain default-disabled for real Run context. There is still **no** public submit, medically validated result writer/schema, real provider call, clinical release, or agreed result-erasure policy. Do not enable the internal gate for debugging.

A completed analysis may continue to resolve the same image **after its submission review window expires** only while that analysis result is separately valid, the original still exists, and owner/baby/thread/session, photo fingerprint, current review status, and revocation checks pass. Review expiration never renews the original approval for a **new** submission. `context_expires_at`, account/photo deletion, review-policy revocation and privacy retention remain independent gates. Current tests use synthetic results, not clinical validation.
