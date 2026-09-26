# Product Backend Environment Profiles

Updated: 2026-09-25.

For the single cross-repository file/command matrix (Backend, Agent, and Flutter),
see `app/docs/deployment/environment-workflow.md` from the workspace root.
This page documents the Product Backend-specific contract.

## Environment contract

| Name | Meaning | Source |
| --- | --- | --- |
| `local` | Developer machine | `env/local.env.example` + ignored `env/local.env` |
| `staging` | Internal deployable environment | `env/staging.env.example` + host secret env |
| `production` | Public production environment | `env/production.env.example` + host secret env |
| `test` | Unit/integration tests and ephemeral CI only | Test process / `docker-compose.ci.yml` |

A server release manifest must contain only `staging` or `production`. `test` is
not a server environment.

## Compose ownership

- `docker-compose.local.yml` owns a self-contained developer stack.
- `docker-compose.ci.yml` is an override for ephemeral CI and is never deployed.
- `docker-compose.deploy.yml` is the only staging/production Compose file.
- Product Backend owns PostgreSQL, Redis, MinIO, and the shared Docker network.
- Agent Runtime joins the network externally and uses a separate database,
  Redis identity/database, bucket, and service credentials.

The following non-secret topology values must agree between Backend and Agent:

```text
MOMCOZY_BACKEND_COMPOSE_PROJECT
MOMCOZY_AGENT_COMPOSE_PROJECT
MOMCOZY_NETWORK_NAME
MOMCOZY_BACKEND_API_BIND
MOMCOZY_AGENT_API_BIND
MOMCOZY_POSTGRES_ADMIN_USER
MOMCOZY_AGENT_POSTGRES_DB
MOMCOZY_AGENT_POSTGRES_USER
MOMCOZY_AGENT_MINIO_BUCKET
```

## Secret handling

Examples may contain only obvious `REPLACE_WITH_*` placeholders. Real values
must live in an ignored `env/<environment>.env`, a mode-`0600` host file, or a
GitHub Environment secret. Never put `MOMCOZY_BACKEND_IMAGE` in the env file;
the release workflow injects an immutable digest-qualified image.

Create local config with:

```bash
cp env/local.env.example env/local.env
chmod 600 env/local.env
```

For a server, copy exactly one deployment template:

```bash
cp env/staging.env.example /secure/path/backend.env
# or
cp env/production.env.example /secure/path/backend.env
chmod 600 /secure/path/backend.env
```

## Validation commands

```bash
make backend-productization-status
make backend-staging-config
make backend-production-config
```

The example deployment files render Compose but are intentionally rejected by
`scripts/release.py` until every placeholder secret is replaced.
