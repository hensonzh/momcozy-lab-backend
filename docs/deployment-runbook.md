# Product Backend regional staging release runbook

Updated: 2026-10-21. `local` is not published. `singapore-staging` and
`north-america-staging` are the only deployment targets; both have
`APP_ENV=staging`. Only a verified `main` commit and its immutable digest can
be admitted. No production deployment target exists.

## Singapore staging

`backend-singapore-delivery.yml` is the protected manual workflow. It must
check the exact `main` commit, successful CI image identity and Product OpenAPI
compatibility before using `scripts/release.py`. Its private GitHub Environment
must have reviewers, SSH host-key verification and a region-scoped
`RELEASE_LOCK_PATH`. The host's service env must be mode-`0600`, isolated from
North America, and contain no release-owned image or ID variables. Product
Backend owns PostgreSQL, Redis, MinIO and the shared network; deploy Product
before Agent. Validate `/v1/health/ready` and `/.well-known/jwks.json`, then
Agent cross-service readiness and App smoke, before distributing the APK.

## North America staging

`backend-north-america-validation.yml` validates and publishes an immutable
North America image on a successful `main` push; publication does not deploy.
`backend-north-america-delivery.yml` is the separate, protected manual host
workflow. It requires exact successful Product/Agent push-CI digest artifacts,
verified main commits, approved operator/Environment and explicit apply;
`apply=false` only runs admission. The host must already have prepared clean
release source trees and private configuration. See `deploy/us-east-uat/README.md`
for required GitHub protection, private secrets and host handoff.
The separate `docker-compose.us-east-uat.yml`, private env and
`config/release-targets/north-america-staging.json.example` describe the
regional host. `scripts/check_north_america_env.py` and
`scripts/north_america_release.py` are read-only admissions. The first-release
and update/rollback runners require separate host identity, backups, isolated
restore, schema and public HTTPS gates before an explicit apply. Do not run a
first-release bootstrap on a non-empty host or point Singapore's workflow at
North America.

## Rollback and evidence

Record commit, immutable image digest, migrations, API contract, host, private
env path and readiness evidence per region. Rollback never downgrades the
database schema automatically; confirm client/schema compatibility and verify
restoration before any pointer switch. Retain the prior manifest and backups.
A passing static check or CI build does not prove deployment. This runbook
contains no claim about the current live host state.
