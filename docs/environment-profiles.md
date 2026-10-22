# Product Backend environment profiles

Updated: 2026-10-20. The workspace has one local development target and two
regional staging targets. `test` is an ephemeral CI profile, not a deployment.
There is no production deployment profile.

| Target | Runtime `APP_ENV` | Template | Compose / publication |
| --- | --- | --- | --- |
| `local` | `local` | `env/local.env.example` → ignored `env/local.env` | `docker-compose.local.yml`; no publication |
| `singapore-staging` | `staging` | `env/staging.env.example` → private host env | `docker-compose.deploy.yml`; `backend-singapore-delivery.yml` |
| `north-america-staging` | `staging` | `env/us-east-uat.env.example` → separate private host env | `docker-compose.us-east-uat.yml`; `backend-north-america-validation.yml` builds an immutable image on `main`; host release is a separately controlled operation |

The two regional targets share application code but **never** credentials,
PostgreSQL/Redis/MinIO state, Compose project, network, lock or public origin.
Product Backend owns each target's shared infrastructure; Agent joins the
selected target with its own database, Redis identity and bucket. The existing
US-East host directory and DNS names are physical infrastructure identifiers,
not an additional environment or branch.

Only a validated `main` commit may produce a release image or be admitted by
release tooling. Development branches can run tests but are not publication
sources. A passing CI run or image publication is not a host deployment.

Real credentials belong only in mode-`0600` host env files or protected CI
secrets; templates intentionally contain placeholders. Release scripts inject
immutable image digests and release IDs rather than loading them from private
env. Confirm host state, backups, migration compatibility, TLS and public
readiness separately before any deployment or rollback.

Local setup: `cp env/local.env.example env/local.env && chmod 600 env/local.env`.
Static Singapore Compose check: `make backend-staging-config`. North America
private env check: `python scripts/check_north_america_env.py --env-file
<private-env>`. Neither command proves live service readiness.
