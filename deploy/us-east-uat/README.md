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
  `backend-b-validation.yml` runs on GitHub `dev`; it checks contracts,
  synthetic PostgreSQL/Redis isolation, two-database synthetic dump/restore,
  and locally builds the B Dockerfile. Verify the exact commit's CI result
  before release. The workflow does not publish an image, run migrations
  against UAT or deploy; release integration and live validation remain. B Compose requires `MOMCOZY_B_ENV_MARKER` so A env
  cannot pass static rendering by accident; the read-only `scripts/check_b_env.py`
  also verifies B identity, loopback ports, DB 0, private file mode and no
  placeholders. The marker alone does not prove isolation. `scripts/release.py`
  still refuses B deployment. New `scripts/b_release.py` is a B-only,
  read-only admission check; it validates a clean `dev` source commit, immutable
  image digest, private target declaration and private Backend env, then renders
  B Compose without printing its contents. Use the separate
  `scripts/check_b_pair.py` on the host to verify Product/Agent shared
  credentials and B identities agree before service startup. It does not
  deploy and does not
  prove the source-to-image label, CI provenance or target host state.
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
  and is **not** a persistent-data backup/restore rehearsal. A manifest of
  checksums or an operator-entered restore claim is not evidence of a
  successful isolated restore. `scripts/b_postgres_recovery.py --apply` is
  prepared for the approved B host: it dumps the two B databases and
  restores each on an isolated disposable PostgreSQL server to verify the
  Alembic revision and writes mode-0600 checksummed evidence only after both
  restores succeed. A local two-database synthetic drill passed and B CI
  exercises the same drill on `dev`. It does not cover
  MinIO/Redis, off-host retention or point-in-time restore. It does not prove
  a complete production data restore without a target-host drill. Complete those
  gates before wiring any reviewed deploy operation. The
  `scripts/check_b_rollback.py` is read-only: it checks B-only release
  symlinks/manifests, equal schema revisions and queries the B-owned
  PostgreSQL container for the live Alembic revision. It does not check
  client compatibility beyond the operator flag. This is **not** a rollback
  action; the future runner must recheck under the B release lock immediately
  before switching services. Run the
  Product Alembic migration with appropriate DDL rights, then start and check
  Product API and both workers. Deploy Agent **after** Product is healthy.
  Keep B-specific rollback and schema-compatibility gates; do not downgrade
  databases automatically. Verify `/v1/health/ready` and real UAT mail flow.
- This file does not authorize cloud or server changes. Host capacity was
  measured but 10-run load is untested; domain/TLS, DNS/SMTP and provider egress,
  final bucket names, real secrets, stateful recovery, data-migration strategy
  and actual delivery behavior must still be verified before enabling B. Basic
  TCP connections to `smtp.resend.com:587`, `api.openai.com:443` and
  `ghcr.io:443` succeeded from the host; this does not verify provider
  authentication or real email/model delivery.

Cross-repository plan: `app/docs/deployment/b-us-east-single-host-uat.md`.

## US-East B host preparation (observed 2026-10-01; not deployed)

- JumpServer asset `32.199.186.149` was verified as
  `ubuntu@ip-172-31-29-24` (Ubuntu 24.04.4, amd64, 16 vCPU, 61 GiB RAM).
  `/` has 96 GiB and `/data` is a distinct 200 GiB XFS mount (about 197 GiB
  free before B images). `sudo -n` worked. No A host or A volumes were touched.
- Docker Engine 29.8.2 and Compose 5.5.1 were installed from Docker's signed
  Ubuntu `noble` apt repository after checking its signing-key fingerprint.
  `/etc/docker/daemon.json` sets `data-root` to
  `/data/momcozy-lab-us-east-uat/docker`; containerd's separate image store
  root is `/data/momcozy-lab-us-east-uat/containerd`. Both systemd units
  require `/data` to be mounted. A disposable `hello-world` check passed.
- The mode-0700 B root `/opt/momcozy-lab-us-east-uat` contains separate
  `releases/backend`, `releases/agent`, `shared/backend`, `shared/agent`,
  `current` and `previous` directories. The B release lock and both private
  env and target JSON files are mode 0600. **The env files still contain
  `REPLACE_WITH` secrets and invalid `.example.invalid` URLs; the target
  URLs remain `TBD`.** Their preflight checks reject them as intended.
- A dedicated systemd bind mount maps
  `/data/momcozy-lab-us-east-uat/backups` to
  `/opt/momcozy-lab-us-east-uat/backups`. Its covered mountpoint is mode 000
  when not mounted. Before backup, verify `findmnt --mountpoint
  /opt/momcozy-lab-us-east-uat/backups` and that `df -hT` resolves to `/data`;
  `scripts/b_postgres_recovery.py` also fails closed if the bind mount/source
  is unavailable. **This backup is on the same `/data` device as Docker and
  stateful volumes, not an independent/off-host copy.** Off-host retention
  and recovery still require a separate approved destination and drill.
- Clean GitHub `dev` snapshots were staged under each service's
  `releases/<service>/<commit>` (Backend `e1f65eaf2ea4e997f40e2caa2376438ce71cd0c2`,
  Agent `759ecebdfcb19a8095d9c8abda08d932ede94697`). Only these
  committed snapshots were built locally with the B Dockerfiles and revision
  labels; the pinned MinIO source image was also built. The B PostgreSQL/
  Redis synthetic isolation check passed on this host with no persistent
  volumes or exposed ports. **These locally tagged images are not published
  GHCR digests and do not pass the B release admission.** No `current` pointer,
  business container, database, bucket or public API has been started.

## Private host handoff (placeholders created; not production configuration)

- `scripts/prepare_b_host.py` remains an opt-in scaffold (`--apply`) for a
  fresh host: it creates only B-only mode-0700 directories and mode-0600
  placeholder files and refuses preexisting files. On this host the equivalent
  layout and checksum-verified GitHub `dev` env placeholders were created
  manually; **do not run the scaffold over them or treat them as configured**.

- B-only layout template: `/opt/momcozy-lab-us-east-uat`; B lock at
  `shared/north-america-staging-release.lock`, backend env at
  `shared/backend/north-america-staging.env`, Agent env at
  `shared/agent/north-america-staging.env`. Copy only the corresponding
  `env/us-east-uat.env.example` into each B private file with mode 0600;
  replace every placeholder on the US-East host. Do not put credentials
  in GitHub, Codeup, logs, command arguments or chat. Compose and application
  secrets originate only from private host files; a real delivery must avoid
  printing `docker compose config` or unmasked process environment.
- A separate private target JSON for each service must contain its own
  approved HTTPS origin. The checked-in `.example` remains invalid until
  those domains, DNS and TLS are approved. Keep the target files mode 0600.
- The host architecture, Docker/Compose and on-host backup mount are
  verified. Host UFW is inactive; external security-group exposure was not
  audited. DNS/TLS, approved domains, real credentials, data-disposition
  decision, MinIO/Redis backup and isolated recovery, off-host retention,
  immutable registry images and a reviewed B-only deployment/rollback runner
  remain gates. The synthetic test is not a live backup rehearsal.
