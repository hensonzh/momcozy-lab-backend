# Product Backend Deployment Runbook

Updated: 2026-09-24. This runbook covers both `staging` and `production`.

## Preconditions

1. The target commit is merged into `main`.
2. `backend-ci` completed successfully and published its immutable image
   manifest.
3. The GitHub Environment named `staging` or `production` has required reviewers.
4. The host has a private mode-`0600` Backend env based on the matching template.
5. Product Backend and Agent Runtime use the same network/topology values and
   different database, Redis, MinIO, and service credentials.
6. The public TLS route and CA file are installed before application deployment.

The currently deployed staging DNS still contains `backend-test`; it is a
transitional hostname, while the environment identity is `staging`.

## GitHub Environment configuration

Variables:

```text
staging:
  RELEASE_ROOT=/opt/momcozy-lab
  SERVICE_ENV_FILE=/opt/momcozy-lab/shared/backend/staging.env
  RELEASE_LOCK_PATH=/opt/momcozy-lab/shared/staging-release.lock
production:
  RELEASE_ROOT=/opt/momcozy-lab-production
  SERVICE_ENV_FILE=/opt/momcozy-lab-production/shared/backend/production.env
  RELEASE_LOCK_PATH=/opt/momcozy-lab-production/shared/production-release.lock
both (environment-specific values):
  CA_FILE=/path/to/reviewed/ca.pem
  PUBLIC_URL=https://<backend-host>:8443
```

Copy only the values, not the labels. The Backend and Agent GitHub Environments
for the same target must use the same release root and lock path.

Secrets:

```text
SSH_HOST
SSH_PORT
SSH_USER
SSH_PRIVATE_KEY
SSH_KNOWN_HOSTS
```

`RELEASE_APPROVERS` is a repository variable containing a comma-separated
allowlist. Do not disable GitHub Environment required reviewers for production.

## Bootstrap

Run `.github/workflows/backend-delivery.yml` with:

```text
environment = staging | production
operation   = bootstrap
commit_sha  = <full main SHA with successful backend-ci>
```

Bootstrap creates or verifies PostgreSQL, Redis, MinIO and the environment
network, then initializes service-scoped databases, ACL users and buckets. It
does not publish a mutable image tag and does not deploy Agent Runtime.

## Deploy

Run the same workflow with `operation=deploy`. The workflow:

1. verifies successful CI for the requested full commit and resolves its immutable image digest from GHCR;
2. downloads the public live OpenAPI over the reviewed TLS CA and runs
   `scripts/check_deployed_openapi_compatibility.py` against the candidate
   snapshot. Removed operations, newly mandatory request fields, and removed
   success fields fail closed **before any service or schema mutation**. A
   retired API or stricter password confirmation needs a separate supported-
   client audit and a compatible migration/version strategy; there is no
   emergency bypass in this workflow;
   see [API versioned retirement runbook](api-retirement-runbook.md) for the
   read-only client-exit audit and its currently unmet conditions;
3. transfers an immutable `git archive` snapshot and `scripts/release.py`;
4. acquires the environment release lock;
5. validates the private env and Compose rendering;
6. verifies the image revision label and digest;
7. checks object storage and startup settings;
8. creates a mode-`0600` database backup before a required migration;
9. switches only application services, never reconciles stateful containers;
10. verifies loopback and public `/v1/health/ready`;
11. promotes `${RELEASE_ROOT}/current/backend/release-manifest.json`.

For Agent-backed releases, deploy Backend first and give Agent the promoted
Backend manifest.

## Rollback

Run `backend-delivery` with `operation=rollback` and explicitly confirm schema
compatibility. Rollback changes application code only; it never downgrades the
database. If the previous image cannot read the current schema, roll forward.

## Manual validation only

These commands render the committed contract and do not mutate a server:

```bash
make backend-staging-config
make backend-production-config
```

Direct `docker compose up/down` targets for deployment are intentionally absent.
Emergency host operations must use the same release lock and a reviewed runbook.

## Nginx and network boundary

The app container binds only to the configured loopback address. Nginx owns the
public TLS listener and proxies to that address. PostgreSQL, Redis and MinIO have
no public host ports. The checked-in Nginx template still names the current
staging DNS (`backend-test...`); production must use a separately reviewed site
and certificate rather than copying the staging hostname.

## Verification

After deploy, verify:

```text
GET /v1/health/live
GET /v1/health/ready
GET /.well-known/jwks.json
GET /v1/health/metrics (with service key)
```

Then run `docs/release-smoke-checklist.md`, verify the release manifest
`environment`, commit, image digest and OpenAPI hash, and only then allow Agent
or App delivery to proceed.
