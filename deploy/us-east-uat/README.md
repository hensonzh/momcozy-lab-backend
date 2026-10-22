# North America staging — Product Backend host handoff

Updated: 2026-10-20. This is a regional deployment target, not another
runtime environment: `APP_ENV=staging` and `deployment_target` is
`north-america-staging`. The release source is a clean, verified `main` commit
with an immutable image digest. `deploy/us-east-uat/release-source.json` is the
machine-readable source declaration. A successful
`backend-north-america-validation.yml` image build is **not** host deployment.

The host layout remains `/opt/momcozy-lab-us-east-uat`; this is a physical path,
not a branch or an additional environment. Product owns its isolated
PostgreSQL, Redis, MinIO buckets, network, TLS ingress and release lock.
Agent joins that network with its own database/credentials. Use
`docker-compose.us-east-uat.yml`, a mode-`0600` private Product env generated
from `env/us-east-uat.env.example`, and a separate private target declaration
based on `config/release-targets/north-america-staging.json.example`. Do not
reuse Singapore env, secrets, certificates, Compose project or volumes.

Static admission: `python scripts/check_north_america_env.py --env-file
<private-backend-env>` and `python scripts/north_america_release.py --help`.
These checks must not print secrets or mutate the host. Verify exact source
commit, image digest/labels, Product–Agent contracts, host identity, schema,
backups, isolated restore, ingress/TLS and public readiness independently.

On a **verified empty** host only, the guarded
`scripts/north_america_first_release.py --preflight` can assess first-release
prerequisites; its `--apply` path needs a separate explicit authorization and
must not be run on existing state. For existing services, use
`scripts/north_america_update_release.py --operation update` or `rollback`
without `--apply` to inspect admission first. Schema-changing releases and
unproven restore have no automatic path. A failed release requires inspection,
not blind rerun. Product must be healthy before Agent activation. The optional stool-photo-review-worker runs with image understanding disabled and may process explicit deletion tombstones only; require privacy/policy approval before enabling third-party image review. Complete only
after public HTTPS readiness, worker checks and client-facing smoke pass.

This handoff describes repository contracts. It does not assert what is
currently deployed on a remote host; inspect that host before any release.

## Manual unified host delivery (repository gate; not a deployed state claim)

`backend-north-america-delivery.yml` is **workflow_dispatch on main only**;
`backend-north-america-validation.yml` and
`agent-north-america-validation.yml` publish images/CI digest artifacts only and
never contact the host. The workflow defaults to admission (`apply=false`),
serializes runs, and uses the `north-america-staging` GitHub Environment. Before
using it, a repository administrator must independently configure protected
`main` branch restrictions and required reviewers for that Environment (with
self-approval disabled), and verify these protections are actually enforced.
The Environment needs `NORTH_AMERICA_APPROVERS` (comma-separated GitHub logins),
`NORTH_AMERICA_GH_READ_TOKEN` (read Actions/Packages for **both** repositories),
`NORTH_AMERICA_GHCR_PULL_TOKEN` (read-only GHCR package pull), and the
`NORTH_AMERICA_SSH_HOST`, `_PORT`, `_USER`, `_PRIVATE_KEY`, `_KNOWN_HOSTS`
secrets. The pinned known-hosts key must be independently verified, not
obtained by `ssh-keyscan` during release. Never commit these credentials.

The operator supplies the exact Product and Agent `main` commit IDs. The
workflow confirms main ancestry, matching successful push CI contract/image
jobs, and compares each GHCR image digest with the digest artifact produced by
that exact CI run; missing/expired artifacts fail closed. The host must already
have both source trees under `/opt/momcozy-lab-us-east-uat/releases/{backend,agent}/<commit>`
as clean checked-out `main` commits (a current release may have only its
validated generated manifest), a private env/target pair for each service,
local Docker, pinned stateful images, verified backup storage and the documented
regional layout. It must **not** stage checkouts, infer first-release from
existing containers, initialize secrets, migrate schema during updates, or
repair incomplete first releases automatically. An operator must independently
inspect the host and choose `first-release` **only for a truly empty target**;
otherwise use `update` or a client-compatible `rollback`. Product and Agent
first-release is one operation; updates/rollbacks select a single service.

For update and rollback the entrypoint is the currently active Product source;
only an empty first-release runs from the candidate Product source.
The host entrypoint `scripts/north_america_manual_delivery.py` serializes
another regional manual lock, checks local Docker and the source commit, pulls
exact immutable digests with ephemeral tmpfs Docker credentials, verifies
platform, digest, image ID and labels, then calls the existing first-release or
same-schema update/rollback runner. `apply=true` performs admission again before
applying; rollback also requires `confirm_client_compatible=true`. Preflight
may cache images and create a local lock but does not start application services.
A failed preflight or apply must be inspected manually—never auto-retry or
claim success from successful CI alone. The server-side private configuration,
Environment approval, SSH access, Docker behavior and live readiness remain
**unverified** until a separately authorized host acceptance exercise.
