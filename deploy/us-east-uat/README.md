# Product Backend — US-East UAT (B) single-host strategy

Decision: 2026-09-30. Updated 2026-10-02. B has a guarded first-release
orchestrator but has not been exercised on the target host; subsequent
update/rollback remain separate unfinished work. Retired Kubernetes workloads
and migration Job templates were removed. A's root Dockerfile, Compose
files, private env and GitHub delivery workflow remain unchanged; B must not
call A's `staging` release entrypoint.

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
  This is a new local PostgreSQL instance. **On 2026-10-01 the operator
  confirmed that no historical RDS, Redis or S3 data needs migration.** Do
  not connect to or copy those managed resources. First bootstrap must use
  fresh B-only volumes, databases, Redis state and MinIO buckets.
- Redis is one B service: Product and Agent both use DB 0 with existing
  disjoint key prefixes. B-only ACL denies cross-service keys and DB-switching
  commands; Redis ACL does not provide database-level tenant isolation. Test
  Lua/Stream/lock behavior against a running B container before release. MinIO is one B service with two
  private buckets and separately scoped credentials. No external RDS,
  ElastiCache or managed S3 endpoint belongs in B's private env.
- Use distinct B Compose project names, network, port binds, release root,
  lock and 0600 private env files; do not mount or modify A's data volumes.
  Expose only the two API loopback ports to a B-specific TLS reverse proxy.
  The B HTTPS origins are
  `https://backend-us-dev.lute-momcozylab.luteos.cloud` and
  `https://agent-us-dev.lute-momcozylab.luteos.cloud`; both DNS A records
  resolved to `32.199.186.149` on 2026-10-01. DNS alone is not an ingress or
  certificate check: HTTPS :443 timed out from the operator Mac and target.
  On 2026-10-01 a fresh read-only EC2 inspection of the **attached**
  `launch-wizard-25` security group found no inbound TCP 80 or 443 rules.
  Independent short-lived listeners on both ports on the B host still timed
  out from the operator Mac; UFW was inactive and the host INPUT policy was
  ACCEPT. Coordinate an approved 80/443 ingress change and re-test before
  attempting HTTP-01 issuance or enabling the HTTPS reverse proxy.
  The B containers mount the host's system CA bundle for outbound HTTPS;
  this does not install an ingress TLS certificate or prove public trust.
  The versioned, **not installed** B ingress template is
  `deploy/us-east-uat/nginx-backend.conf`: it listens on :443 with this
  hostname and proxies only to `127.0.0.1:8001`. Agent uses `127.0.0.1:8002`.
  These are host-loopback Docker port publishes, not public port exposures.
  It names a future public
  certificate under `/etc/letsencrypt/live/`; the file is not present yet.
  Install the trusted B-only HTTPS site *before* the first-release runner:
  Nginx may return 502 until the upstream is activated, which is **not**
  readiness. The runner checks the public certificate before touching B state
  and checks real HTTP readiness after each activation. This avoids requiring
  a running API as a prerequisite for installing its own reverse proxy. Do
  not enable a fake/self-signed public endpoint to bypass TLS verification.
  PostgreSQL, Redis and MinIO must not expose public host ports. Build the
  pinned MinIO source image for this B tag before first bootstrap:
  `docker build --platform linux/amd64 -f deploy/us-east-uat/Minio.Dockerfile -t momcozy-us-east-uat-minio:9e49d5e7a648f00e deploy/shared`.
  Do not substitute a mutable public image tag.
- Before the **first** stateful bootstrap, run the read-only
  `python scripts/check_b_fresh_bootstrap.py` on the target with Docker access.
  It refuses any named or Compose-labeled B volumes, network or containers,
  including stopped ones. On 2026-10-01 an equivalent target-host check found
  none. This check does not inspect arbitrary unlabelled disk paths, cannot
  justify deleting a conflicting resource, and is not suitable for later
  releases after B's stateful stack exists.

## Release handoff

- B source: GitHub `dev`, declared in `release-source.json`. Build
  from repo root with `docker build -f deploy/Dockerfile .`, pin the published
  image digest and use a separate approved US-East host and release root.
  `deploy/config_us-east-uat` is **non-secret defaults only**. B Compose and
  private env templates now exist. The B-only GitHub `dev`
  `backend-b-validation.yml` runs on GitHub `dev`; it checks contracts,
  synthetic PostgreSQL/Redis isolation, two-database synthetic dump/restore,
  Redis RDB and MinIO two-bucket synthetic backup/isolated restores, and
  locally builds the B Dockerfile. After those gates pass on a GitHub `dev`
  push, the separate `b-image` job publishes only the B Dockerfile image to
  private GHCR as `b-dev-<full SHA>`, then inspects and runs offline checks
  against the exact published digest before recording it. Verify the exact
  commit's successful job and digest before release. It does not run migrations
  against UAT or deploy; release integration and live validation remain.
  B Compose requires `MOMCOZY_B_ENV_MARKER` so A env
  cannot pass static rendering by accident; the read-only `scripts/check_b_env.py`
  also verifies B identity, loopback ports, DB 0, private file mode and no
  placeholders. The marker alone does not prove isolation. `scripts/release.py`
  still refuses B deployment. New `scripts/b_release.py` is a B-only,
  read-only admission check; it validates a clean `dev` source commit, immutable
  image digest, private target declaration and private Backend env, then renders
  B Compose without printing its contents. Its subprocess uses a B-only
  allowlisted environment; mutating B host scripts explicitly bind Docker to
  the local Unix socket and reject inherited remote context/host overrides.
  Do not bypass them by running Compose from an interactive shell where A's
  variables may override `--env-file`. The
  admission validates Product/Agent shared credentials and B identities
  before startup; it does not deploy and does not
  prove the source-to-image label, CI provenance or target host state.
- **First release only**: `scripts/b_first_release.py --preflight` is read-only;
  it checks target identity, fresh Docker state, trusted TLS, local image
  identities and the B backup bind mount without starting services. Once
  independently reviewed, `scripts/b_first_release.py --apply` requires both
  immutable image digest/commit pairs, the matching imported local OCI image
  IDs, clean checked-out `dev` release directories, private B env/target files
  and a pre-existing trusted public TLS ingress. It refuses any existing B
  Compose state, then runs B-only infrastructure bootstrap, two Alembic
  migrations, *fresh* PostgreSQL/Redis/MinIO isolated restores, Product
  activation/public HTTPS readiness, and Agent activation/public readiness.
  A private, dedicated first-release lock covers the whole sequence, while
  the existing B stage lock remains unchanged. Any failed stage stops without
  an automatic downgrade or deletion. Do not
  rerun after partial state without an explicit recovery review. This entry
  does not install Nginx/certificates or fetch/import images; use the approved
  GHCR digest-to-OCI procedure first. Existing deployment and schema changes
  still require a separate reviewed update/rollback runner. SMTP/model
  provider E2E and 10-run load are independent acceptance checks, not gates
  on creating App CI artifacts.
- B self-hosted PostgreSQL and Redis Compose images and their isolated restore
  drills use the same fixed Docker Hub digest. B's separate
  `deploy/us-east-uat/Minio.Dockerfile` pins its Go/Alpine bases; A continues
  using `deploy/shared/Minio.Dockerfile` unchanged. The local B MinIO tag is
  accepted only after checking its upstream revision; record the actual B
  image ID during target-host preflight and do not substitute a public tag.
  The Go/Alpine bases are digest-pinned, but APK package resolution and the
  MinIO build itself are not byte-for-byte reproducible; verify the target
  image identity before stateful bootstrap.
- The private Backend env must provide B-only credentials, service names and
  URLs (Product `DATABASE_URL`, `REDIS_URL` ending `/0`, MinIO bucket and
  scoped keys, JWT/service keys, Resend SMTP key). Use the same B identity
  contract as Agent, not A's `env/staging.env`. Never commit a populated env,
  credentials or generated Compose rendering to Git.
- Run `python scripts/check_b_env.py --env-file /absolute/path/to/private-backend.env`
  then the B-only read-only `scripts/b_release.py` admission, which renders
  Compose with a restricted subprocess environment. Do not run raw `docker compose`
  from an interactive shell: shell variables can override the private env.
  Static checks do not verify provider connectivity, capacity or actual TLS.
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
  MinIO, Redis live data, off-host retention or point-in-time restore.
  `scripts/b_redis_recovery.py --apply` similarly acquires the B release lock,
  checks the dedicated `/data` backup bind mount, captures a fresh RDB from the
  B-owned Redis container without putting its ACL password in host arguments,
  and verifies key count after restoring in a disposable no-network/no-volume
  container. A Redis ACL/stream/lock synthetic contract test also runs; the
  RDB drill has a separate CI synthetic test with Product/Agent keys. **Neither
  synthetic drill proves a target-host backup or lossless recovery of live
  changing data**; Redis AOF history is not preserved by the RDB copy.
  Verified on-host PostgreSQL, Redis and MinIO backups with isolated
  recovery remain release gates. Off-host retention is deferred and does not
  block B release; do not mistake on-host backups for independent disaster
  recovery. The first-release runner executes these gates only after target
  preflight; it has not run on the host. The `scripts/check_b_rollback.py` is
  read-only: it checks B-only release
  symlinks/manifests, equal schema revisions and queries the B-owned
  PostgreSQL container for the live Alembic revision. It does not check
  client compatibility beyond the operator flag. This is **not** a rollback
  action; the future runner must recheck under the B release lock immediately
  before switching services. On the *first empty* B deployment, migrate both
  databases with separate DDL roles, prove the fresh recovery of both databases
  and shared stores, then start and check Product API and both workers. Activate
  Agent **after** Product is publicly healthy.
  Keep B-specific rollback and schema-compatibility gates; do not downgrade
  databases automatically. Verify `/v1/health/ready` and real UAT mail flow.
- This file does not authorize cloud or server changes. Host capacity was
  measured but 10-run load is untested; HTTPS/TLS ingress, SMTP and provider authentication,
  final bucket names, real secrets, stateful recovery,
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
  env and target JSON files are mode 0600. The approved B domains have been
  entered in both private env and target declarations. On 2026-10-01, the
  isolated host generated its own PostgreSQL, Redis, MinIO, JWT, email-token
  and internal service credentials in the two mode-0600 env files. A rerun
  did not rotate them. Later that day, Resend SMTP and the A-chain OpenAI key
  were installed in B's private env without committing or printing their
  values. SMTP/STARTTLS login and OpenAI `/v1/models` authentication passed
  **from the B host**; no mail or model inference was sent. B now uses
  regular email registration/login: `AUTH_INVITE_LOGIN_ENABLED=false`
  and an empty `AUTH_INVITE_CODES`. After commit
  `a9842f9c2bd591ece61c94308751c6dfc3cf7a64` passed B CI, the existing
  private Backend env was updated in place without rotating credentials.
  Backend and Agent static env checks, the cross-service pair check and B
  Backend Compose quiet rendering passed. Both B HTTPS origins still time out;
  no public certificate is on the host. Nginx and Certbot packages were
  installed, but the package-default Nginx site was immediately stopped and
  disabled; no B site was enabled. No services or B release pointers have
  been started.
- A dedicated systemd bind mount maps
  `/data/momcozy-lab-us-east-uat/backups` to
  `/opt/momcozy-lab-us-east-uat/backups`. Its covered mountpoint is mode 000
  when not mounted. Before backup, verify `findmnt --mountpoint
  /opt/momcozy-lab-us-east-uat/backups` and that `df -hT` resolves to `/data`;
  `scripts/b_postgres_recovery.py` also fails closed if the bind mount/source
  is unavailable. **This backup is on the same `/data` device as Docker and
  stateful volumes, not an independent/off-host copy.** Off-host retention
  is not required for B release as of 2026-10-01; providing it later still
  requires a separate approved destination and recovery drill. A host or
  shared-disk failure can lose both live data and these backups.
- Clean GitHub `dev` snapshots were staged under each service's
  `releases/<service>/<commit>`. The latest staged and locally built snapshots
  after the B validation CI passed were Backend
  `62ea6c0d29620d0ed07483c79abf80f3be58847a` and Agent
  `a689e662a21e91bcb95d4180e671cd5869c94154`. An additional clean Backend
  snapshot `b8339eb79f88bd590f360e61f9c38e5d5ef736b5` passed GitHub B
  contract and B-image CI and was verified on the host for the one-time
  credential script; it was **not deployed**. Earlier snapshots remain
  untouched. Both B images carry their source revision label; the pinned MinIO
  source image was also built. The B PostgreSQL/Redis synthetic isolation
  check passed on this host with no persistent volumes or exposed ports.
  **These locally tagged images are not GHCR digest references and do not
  pass the B release admission.** The CI-published B images must be verified
  separately by exact commit and digest before any use on the host. No
  `current` pointer, business container,
  database, bucket or public API has been started.

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
- **One-time B-owned credential provisioning:** as the owning `ubuntu` user on
  the approved US-East host, run
  `python3 scripts/provision_b_private_credentials.py --apply` from a verified
  clean GitHub `dev` Backend snapshot. It takes the B release lock, checks
  private owners/modes and B identity, creates distinct PostgreSQL, Redis,
  MinIO, JWT, email-token and internal service credentials in the two
  existing mode-0600 files, and checks cross-service shared values. A rerun
  never rotates; partial provisioning fails closed for manual recovery.
  It does not read A credentials, create Docker volumes, start services or
  configure external providers. Resend `AUTH_SMTP_PASSWORD` and the Agent model
  API key were subsequently installed as separate, approved provider handoffs.
  B's consumer login is email-only; no universal invite code is generated.
  Never print the private files or capture the generated values in CI/logs.

- B normal-user auth is email registration, verification, login and password
  reset. B's private Backend env must contain `AUTH_INVITE_LOGIN_ENABLED=false`
  and `AUTH_INVITE_CODES=` (an explicitly empty value). Runtime rejects an
  invite-login attempt when disabled, and B release admission requires both
  values; the legacy A lane keeps its existing invite-only behavior. Do not reuse A's fixed invite code
  and do not generate a token merely to satisfy an old env placeholder.
- A separate private target JSON for each service contains its own approved
  HTTPS origin. The checked-in `.example` now documents those non-secret
  origins; the private declarations remain mode 0600. A valid static target
  check is **not** evidence of a public certificate or running service.
- The host architecture, Docker/Compose and on-host backup mount are
  verified. Host UFW is inactive; external security-group exposure was not
  audited. HTTPS/TLS, real email delivery, 10-run load evidence, live
  PostgreSQL/MinIO/Redis backup and isolated recovery,
  digest verification on host and a reviewed B-only update/rollback
  runner remain gates. The guarded first-release orchestrator is code-complete
  but has not yet passed its host exercise. Off-host retention is deferred, not a release gate.
  The fresh-only Docker guard passed using `sudo -n`; unprivileged `ubuntu` Docker access failed, so the future
  runner must explicitly resolve its privilege model. The synthetic test is
  not a live backup rehearsal.

## 2026-10-01 port 8001/8002 and ingress preflight

- The B-only `dev` Backend/Agent sources and target private env now both use
  `127.0.0.1:8001` / `127.0.0.1:8002`. Only those four non-secret bind lines
  changed in the mode-0600 private files under the B release lock. Both
  static env checks, cross-service pair check, quiet Compose renders and
  read-only B release admissions passed for the CI-published commit/digest
  pairs. **These checks did not deploy images, databases, workers or Nginx.**
- Verified attached EC2 group `launch-wizard-25` has exactly one inbound
  rule: TCP 22. There is no inbound 80 or 443 rule. Temporary host listeners
  on 0.0.0.0:80 and :443 still timed out from the operator Mac; probes were
  removed. UFW is inactive and iptables INPUT policy is ACCEPT. Request an
  approved SG change for 80 (certificate issuance/renewal) and 443 (HTTPS),
  then confirm external reachability before public issuance. NACL read-only
  inspection was denied by IAM; don't claim it has been cleared.
- Ubuntu `nginx` and `certbot` are installed on the B host. The package's
  default Nginx site started automatically; it was stopped and disabled
  immediately. Certbot's timer is installed, but there are no B certificates
  and no enabled B HTTPS server blocks. Do not run a production ACME request
  before external port 80 is demonstrably reachable. Do not substitute a
  self-signed certificate. The checked-in B Nginx templates require the real
  certificates and reviewed unknown-host policy before installation; a 502
  until business containers start is not public readiness.
- The GHCR images were published by B CI as immutable digests, but the host
  currently gets `denied` when it tries to inspect those private digests.
  Configure an approved read-only GHCR pull identity on the B host without
  writing a token to command arguments, logs or Git. The `b_release.py`
  entrypoints perform static admission only. `b_first_release.py` sequences
  first bootstrap, real on-host isolated restores and activation but has not
  been run on this host. Updates and rollback need a separate reviewed runner.
