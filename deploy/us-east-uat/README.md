# Product Backend — US-East UAT (B) single-host strategy

Decision: 2026-09-30. This is a **design and handoff**, not an executable
release. The retired B Kubernetes workloads and migration Job templates were
removed. A's root Dockerfile, Compose files, private env and GitHub delivery
workflow remain unchanged; B must not call A's `staging` release entrypoint.

## Ownership and isolation

- The B Product Backend Compose project on the approved US-East host owns one
  PostgreSQL, one Redis and one MinIO service and their **B-only named volumes**.
  The B-only PostgreSQL init revokes PUBLIC CONNECT on each application DB;
  this init runs only on a new volume, not on existing data.
  Product API, notification worker, auth-email worker and a one-shot migration
  container use the same immutable Backend image digest.
- PostgreSQL contains `momcozy_lab_backend_uat` and
  `momcozy_lab_agent_uat` as separate databases/roles. The Agent Compose
  project joins the B network; it does **not** start another stateful stack.
  This is a new local PostgreSQL instance: the former managed RDS databases
  are **not** migrated automatically. Decide whether historical data must be
  migrated before any schema initialization.
- Redis is one B service: Product and Agent both use DB 0 with existing
  disjoint key prefixes. B-only ACL denies cross-service keys and DB-switching
  commands; Redis ACL does not provide database-level tenant isolation. Test
  Lua/Stream/lock behavior against a running B container before release. MinIO is one B service with two
  private buckets and separately scoped credentials. No external RDS,
  ElastiCache or managed S3 endpoint belongs in B's private env.
- Use distinct B Compose project names, network, port binds, release root,
  lock and 0600 private env files; do not mount or modify A's data volumes.
  Expose only the two API loopback ports to a B-specific TLS reverse proxy.
  PostgreSQL, Redis and MinIO must not expose public host ports. Build the
  pinned MinIO source image for this B tag before first bootstrap:
  `docker build -f deploy/shared/Minio.Dockerfile -t momcozy-us-east-uat-minio:9e49d5e7a648f00e deploy/shared`.
  Do not substitute a mutable public image tag.

## Release handoff

- B source: GitHub `dev`, declared in `release-source.json`. Build
  from repo root with `docker build -f deploy/Dockerfile .`, pin the published
  image digest and use a separate approved US-East host and release root.
  `deploy/config_us-east-uat` is **non-secret defaults only**. B Compose and
  private env templates now exist. The B-only GitHub `dev`
  `backend-b-validation.yml` is prepared locally; after commit/push it will
  check contracts, synthetic PostgreSQL/Redis isolation and locally build
  the B Dockerfile; it does not publish an image,
  run migrations against UAT or deploy. Release integration and live validation
  still remain. B Compose requires `MOMCOZY_B_ENV_MARKER` so A env
  cannot pass static rendering by accident; the read-only `scripts/check_b_env.py`
  also verifies B identity, loopback ports, DB 0, private file mode and no
  placeholders. The marker alone does not prove isolation. `scripts/release.py`
  still refuses B deployment.
- The private Backend env must provide B-only credentials, service names and
  URLs (Product `DATABASE_URL`, `REDIS_URL` ending `/0`, MinIO bucket and
  scoped keys, JWT/service keys, Resend SMTP key). Use the same B identity
  contract as Agent, not A's `env/staging.env`. Never commit a populated env,
  credentials or generated Compose rendering to Git.
- Run `python scripts/check_b_env.py --env-file /absolute/path/to/private-backend.env`
  before `docker compose --env-file /absolute/path/to/private-backend.env -f docker-compose.us-east-uat.yml config --quiet` (set `MOMCOZY_BACKEND_ENV_FILE`
  and immutable `MOMCOZY_BACKEND_IMAGE` outside the file). Static checks do not
  verify credentials, provider connectivity, resource capacity or actual TLS.
- Before starting application containers: initialize two DBs/roles, Redis
  ACLs and two buckets; validate an isolated backup and restore plan. The
  synthetic `scripts/check_b_infra_contract.sh` uses ephemeral local containers
  and is **not** a persistent-data backup/restore rehearsal. Run the
  Product Alembic migration with appropriate DDL rights, then start and check
  Product API and both workers. Deploy Agent **after** Product is healthy.
  Keep B-specific rollback and schema-compatibility gates; do not downgrade
  databases automatically. Verify `/v1/health/ready` and real UAT mail flow.
- This file does not authorize cloud or server changes. Host capacity, domain
  and TLS, DNS/SMTP egress, final bucket names, secrets, volumes, migration
  strategy and actual Jenkins behavior must be verified before enabling B.

Cross-repository plan: `app/docs/deployment/b-us-east-single-host-uat.md`.
