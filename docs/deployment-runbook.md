# Product Backend Deployment Runbook

This runbook covers the Product Backend and the test infrastructure it owns.
Agent Runtime is deployed from a separate repository and has its own database,
workers, model configuration, evals, and run-recovery procedures, while sharing
the same PostgreSQL, Redis, and MinIO service instances.

The current shared server profile is `test`; automated CI uses a separate,
ephemeral `momcozy-lab-backend-ci` Compose project and is not a deployable
environment. Build release images under the environment-neutral
`momcozy-lab-backend` repository and tag them with an immutable commit or digest
rather than an environment name. The `backend-test` and `agent-test` DNS labels,
Compose/env profile, runtime metadata, and release manifest therefore use the
same `test` identity.

The environment rename is a controlled cutover, not an in-place alias. The
test Compose file creates `momcozy_test` / `agent_runtime_test`, test buckets,
and test-named volumes; it does not reuse legacy `momcozy-lab-*-staging`
resources. If legacy containers still own `127.0.0.1:8001` or the previous
network, the collision gate must stop the release. Back up and restore-test the
legacy data, then obtain explicit authorization either to reset it or migrate
it before bootstrapping this profile.

## Required IDs

Start release and incident debugging from these IDs when available:

- `request_id`
- `actor_user_id`
- `resource_id`
- `action_id`
- `idempotency_key`

Agent Runtime-owned `thread_id` and `run_id` may be supplied as cross-service
correlation values, but they are not Product Backend execution state.

## Preflight

1. Confirm the private test configuration includes:
   - `APP_ENV=test`
   - distinct PostgreSQL admin/Product/Agent passwords;
   - distinct Redis admin/Product/Agent passwords;
   - MinIO root credentials plus distinct Product/Agent access-key pairs;
   - Product Backend `DATABASE_URL` on `momcozy_test`
   - Product Backend `REDIS_URL` on logical DB 0
   - Product Backend bucket `momcozy-test`
   - `AUTH_JWT_PRIVATE_KEY_B64`
   - `AUTH_JWT_ISSUER`
   - `AUTH_JWT_PRODUCT_AUDIENCE`
   - `AUTH_JWT_RUNTIME_AUDIENCE`
   - one or more private test `AUTH_INVITE_CODES`
   - `AGENT_RUNTIME_SERVICE_API_KEY`
   - `AGENT_MODEL_ASSET_PUBLIC_BASE_URL`
   - `AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS`
   - explicit `CORS_ALLOWED_ORIGINS` and `TRUSTED_HOSTS`
   - environment-appropriate rate limits and upload limits
2. Confirm the Product Backend is the only holder of the JWT private key.
3. Confirm PostgreSQL, Redis, and MinIO have no host/public port mappings.
4. Confirm `GET /.well-known/jwks.json` returns only public RSA fields and the
   expected active key id.
5. Confirm the Agent Runtime service key differs from the general operator service
   key and is available only to the two services.
6. Confirm `backend-ci` passed every test, migration, integration, container,
   backup-hook, and OpenAPI drift gate for the exact full commit.
7. Obtain the CI image manifest and copy its digest-qualified
   `ghcr.io/hensonzh/momcozy-lab-backend@sha256:...` reference. Do not rebuild
   or retag the image on the server.
8. Confirm the Product Backend origin is
   `https://backend-test.lute-momcozylab.luteos.cloud:8443`, capability paths
   are redacted from application logs, and the inactivity TTL remains 1800
   seconds unless a reviewed product requirement changes it.
9. Run:

   ```bash
   make backend-productization-status
   make backend-test-smoke
   ```

## Standard Test Delivery

The host layout is fixed under `/opt/momcozy-lab`:

- private configuration: `/opt/momcozy-lab/shared/backend/deploy.env`, mode
  `0600`;
- immutable source snapshots:
  `/opt/momcozy-lab/releases/backend/<full-commit>`;
- current and previous pointers: `/opt/momcozy-lab/current/backend` and
  `/opt/momcozy-lab/previous/backend`;
- database backups: `/opt/momcozy-lab/backups/backend`;
- archived release manifests: `/opt/momcozy-lab/manifests`.

Test delivery is an explicit manual dispatch from `main`. Before deployment
secrets are available, the workflow checks both the original actor and the
re-run actor against the comma-separated `TEST_APPROVERS` repository variable.
Missing, malformed, or unauthorized operator configuration fails closed.
The current allowlist is `hensonzh`. No second issue comment or polling wait is
required; the old `TEST_APPROVAL_ISSUE` variable is no longer consumed.

CI builds and smoke-tests the image on the publishing runner after prerequisite
gates pass, verifies its OCI revision, then pushes that same image to GHCR.
Only the small immutable digest manifest is uploaded to Actions; no image tar
is saved, uploaded, downloaded, or loaded by another job.

Keep the `test` environment for deployment records and configure
`TEST_SSH_HOST`, `TEST_SSH_PORT`, `TEST_SSH_USER`,
`TEST_SSH_PRIVATE_KEY`, and `TEST_SSH_KNOWN_HOSTS` as test-scoped
secrets where the plan supports them, otherwise as repository secrets. The host
must already be authenticated to pull the private GHCR package, and the
deployment user must own the release paths and be allowed to run Docker. The
known-hosts value is mandatory; host-key checks are never disabled.

Run `.github/workflows/backend-test-delivery.yml` manually with:

1. `operation=deploy`;
2. the full 40-character commit already merged into `main` and published by a
   successful `backend-ci` push run.

`scripts/test_release.py` rejects a mutable tag, a different release root,
an image whose OCI revision label differs from the commit, an occupied
`127.0.0.1:8001`, or a test network owned by another Compose project. The
workflow checks out trusted release tooling from the immutable trigger SHA,
proves that the requested commit is reachable from `main`, and downloads the exact image
manifest from that commit's successful CI run. Workflow inputs enter shell only
through quoted environment variables. Backend and Agent deliveries serialize
on `/opt/momcozy-lab/shared/test-release.lock`.

A retry reuses an existing release directory only when its source-archive and
extracted-tree checksums match. Deploy verifies the already-running shared infrastructure
without reconciling it, compares the database revision with the image's unique
Alembic head, and creates a private backup plus runs migration only when the
revision changes. It then verifies object-storage write/read/delete, replaces
only the API, checks local and public readiness, and atomically advances the
release pointer. A failed switch restores the exact image from the current
manifest. The manifest—not `deploy.env`—is the canonical image identity for
restart and rollback.

Re-running the already-current commit refreshes its manifest without replacing
the distinct `previous/backend` rollback pointer.

On a brand-new server, run the same workflow once with `operation=bootstrap`
before `operation=deploy`. Bootstrap is the only normal path that creates the
shared PostgreSQL, Redis, and MinIO containers and service-scoped MinIO users;
it is deliberately separate from application delivery.

For `operation=rollback`, set `confirm_schema_compatible=true`. Rollback loads
the previous manifest and image digest and replaces only the API. It does not
run an Alembic downgrade. If the previous code cannot read the current schema,
roll forward instead.

## Release Semantics

1. Do not use a bare `docker compose up` as a release or recovery mechanism.
   Use the protected workflow; use `test_release.py restart-current` only for
   an audited host-side restart so image identity is derived from the current
   manifest.
2. The current baseline targets an empty database and does not support
   upgrading an older Product Backend schema. Do not point it at a database
   whose data must be preserved without an explicit migration plan.
   The release-owned tools job is the only path that invokes
   `python -m alembic -c alembic.ini upgrade head`.
3. Wait for `GET /v1/health/live`.
4. Wait for `GET /v1/health/ready`; readiness must verify configured PostgreSQL
   and Redis.
5. Run `docs/release-smoke-checklist.md`.
6. Verify the JWKS and internal Agent Runtime API boundary before allowing a separately
   deployed Agent Runtime to use the new Product Backend contract.
7. Watch 5xx rate, latency, auth failures, database/Redis latency, and object
   storage failures for at least one SLO window.

Product Backend deployment does not start or manage Agent Runtime processes.
Product Backend Compose does not start an Agent Runtime worker; Agent Runtime is independently
deployed.

## Test Docker Compose

`docker-compose.test.yml` is the only test infrastructure owner. It creates:

- Docker network `momcozy-lab-test`;
- PostgreSQL databases/roles `momcozy_test` and `agent_runtime_test`;
- Redis, with a disabled default user, a private admin account, and separate
  Product/Agent ACL users restricted to their key prefixes; Product Backend
  uses logical DB 0 and Agent Runtime uses logical DB 1;
- MinIO buckets `momcozy-test` and `agent-runtime-test`, each with a
  bucket-scoped service user; application containers never receive root keys;
- Product Backend alias `product-backend` and host bind `127.0.0.1:8001`.

Generate URL-safe secrets, copy the example, fill every empty
`MOMCOZY_TEST_*` and application-secret value, set the private file to mode
`0600`, then use the protected workflow's `bootstrap` operation followed by
`deploy`. The image digest is deliberately absent from the private env; the
workflow obtains it from the successful CI artifact.

Then copy only the Agent-scoped PostgreSQL password, Redis password, and MinIO
access-key pair into the private Agent Runtime test env and deploy Agent
Runtime from its repository.
Agent Runtime joins the existing network; it must not create another
PostgreSQL, Redis, or MinIO service.

The release script creates database dumps as mode `0600` under a mode `0700`
directory only when the Alembic revision changes and retains the latest ten
successful pre-migration dumps. Stateful credential or image changes are
planned maintenance: ordinary application deploys never recreate these
containers before backup.

Direct test mutation targets in the Makefile intentionally fail closed.
Stopping shared infrastructure, rotating its credentials, or resetting its
volumes is a separate approved maintenance procedure: first stop Agent Runtime,
acquire the shared host lock, derive the exact Compose/image identity from the
current manifest, and complete the required backup. An application delivery
must never perform these operations.

The PostgreSQL init script runs only for an empty named volume. It does not
upgrade an older test volume or rotate existing role passwords; those
operations require an explicit migration/credential-rotation procedure.

No production deployment profile is currently shipped. Production application
safeguards remain in code until a separate production design is approved.

## Agent Runtime Integration Boundary

The independent Agent Runtime uses only:

- `GET /v1/internal/agent/profile` for the current Run's basic profile context.
- `POST /v1/internal/agent/files/resolve` for model attachments.

Both require the Runtime-only service key and explicit actor scope. Business
Tool and Action endpoints have been removed; Runtime cannot write Product data.

The retirement migration `20260916_0019` removes both diary tables and prenatal
plans/tasks, preserving feeding/pumping facts by clearing retired task links.
Reports using removed diary sources are cancelled and their derived content is
cleared. Downgrade recreates empty tables only; historical rows require a backup.

`POST /v1/internal/agent/files/resolve` validates both `actor_user_id` and
`file_id` ownership before returning an opaque Product Backend capability URL.
Agent Runtime must retain the stable Product Backend `file_id`, not persist the capability URL as
identity.

## Nginx Edge Proxy

`deploy/nginx/momcozy-lab-product-backend.conf` is the versioned Product
Backend site template. It owns only
`backend-test.lute-momcozylab.luteos.cloud:8443` and proxies it to
`127.0.0.1:8001`. Keep the application bound to loopback and expose it only
through the host Nginx listener. The Agent Runtime site is maintained by the
`agent/` repository as `momcozy-lab-agent-runtime.conf`.

The host must keep exactly one separate unknown-host rejection site. Individual
service files must not declare `default_server`, because all legacy and new
sites share the same host listener. The shared test leaf certificate at
`/etc/nginx/tls/momcozy-lab-test/fullchain.pem` must contain both DNS SANs:

- `backend-test.lute-momcozylab.luteos.cloud`
- `agent-test.lute-momcozylab.luteos.cloud`

Its private key is `/etc/nginx/tls/momcozy-lab-test/privkey.pem`. Both Nginx
sites deliberately reference this one SAN certificate, while SNI selects the
correct service site and upstream.

The `/v1/model-assets/` location disables Nginx access logging because its path
contains a bearer capability. The container also disables Uvicorn's raw access
log; the application emits its own redacted route log and request metric.
Preserve both controls in any replacement process manager, ingress, or CDN.

Install the Product Backend site under a service-specific host filename. Do not
replace the existing legacy `momcozy-api` site:

```bash
sudo install -m 0644 deploy/nginx/momcozy-lab-product-backend.conf \
  /etc/nginx/sites-available/momcozy-lab-product-backend
sudo ln -sfn /etc/nginx/sites-available/momcozy-lab-product-backend \
  /etc/nginx/sites-enabled/momcozy-lab-product-backend
sudo nginx -t
sudo systemctl reload nginx
curl --fail --show-error \
  --resolve backend-test.lute-momcozylab.luteos.cloud:8443:127.0.0.1 \
  --cacert /etc/nginx/tls/momcozy-ca/ca.pem \
  https://backend-test.lute-momcozylab.luteos.cloud:8443/v1/health/live
```

Never publish the Product Backend container port, Postgres, Redis, or object storage
directly. Agent Runtime reaches the internal API through an approved private
network or service gateway and must not share a public mobile route.

## Rollback

1. Stop routing traffic to the new Product Backend version.
2. Roll back the Product Backend image.
3. Do not downgrade schema unless a tested downgrade exists.
4. For expand-contract changes, keep backward-compatible schema and response
   fields until both Product Backend and Agent Runtime callers have moved.
5. If the internal Agent Runtime API contract caused the incident, disable Agent
   Runtime traffic at the service gateway or rotate/revoke its service key while
   Product Backend APIs recover.
6. Add a Product Backend contract regression test before the next release.

## Product Backend Dependency Incident

1. Check `/v1/health/ready`.
2. Check `/v1/health/metrics` with the operator `X-Service-Key`.
3. Verify Postgres, Redis, and object storage independently.
4. Search logs by `request_id`; for Agent Runtime-originated writes, also correlate
   `actor_user_id` and `action_id`.
5. Retry only idempotent operations with the original `Idempotency-Key`.
6. Do not operate on Agent Runtime queues, runs, memory, or model-provider state from
   the Product Backend.

## Security Incident

1. Revoke affected Product Backend device sessions or refresh-token families.
2. Rotate the Agent Runtime service key, operator service key, or object-storage
   credentials when implicated.
3. Search audit logs by `actor_user_id`, `actor_service`, `action_id`, and
   `request_id`.
4. Preserve evidence with redaction; never copy secrets, health content,
   signed URLs, or model-asset capability URLs into tickets.
5. Add a contract or security regression test.

## Backup And Restore Drill

1. Confirm Product Backend PostgreSQL backup completion and retention.
2. Confirm object-storage retention for uploads and product assets.
3. Run:

   ```bash
   python scripts/check_backup_restore_hooks.py --strict
   ```

4. Restore into an isolated environment.
5. Run migrations to the current head.
6. Run `/v1/health/ready` and the Product Backend release smoke checklist.
7. Record actual RPO/RTO.

Agent Runtime persistence is backed up and restored by the Agent Runtime team;
do not assume the Product Backend database contains conversation or run state.

## Credential Rotation Drill

1. Create new Product Backend infrastructure, JWT signing, or service credentials in
   the secret manager.
2. For JWT signing changes, coordinate with Agent Runtime because it validates
   Product Backend access tokens through JWKS.
3. For Agent Runtime service-key changes, deploy the new shared value to both services
   in a coordinated maintenance window.
4. Run readiness, JWKS, internal API authentication, owner-scope, and
   idempotency smoke checks.
5. Check auth, object storage, dependency, and 5xx metrics.

The current single-key JWKS contract does not provide an overlapping signing-key
window. Add current/previous key publication before relying on zero-downtime JWT
rotation. Never put old or new credentials in logs, tickets, commits, or
OpenAPI examples.

### Worker lifecycle during test delivery

Deploy, restart, and rollback reconcile API, notification, and auth-email workers
from the same immutable image. The report worker starts when both
`CARE_REPORT_RUNTIME_URL` and `CARE_REPORT_SERVICE_KEY` are configured; the video
worker starts when `CONSULTATION_VIDEO_PROVIDER` is enabled. Disabled workers
are stopped. Historical source snapshots only select services defined by that
snapshot, and failed candidate workers are stopped before restoring a previous
release. PostgreSQL, Redis, and MinIO are never restarted by this switch.

Starting a mail worker does not configure SMTP; complete the account-auth fields
in the private env before testing email delivery. Readiness alone does not prove
SMTP, Google login, FCM, or report generation works.
