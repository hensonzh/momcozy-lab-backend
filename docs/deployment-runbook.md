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

## Cry analysis dependency

The Product Backend owns `POST /v1/cry-analyses` (`202 Accepted`) and commits a
bounded, owner-scoped `pending` result and idempotency reference before sending
PCM to the independent Zoundream classifier over TLS (table migration
`20261022_0027`, status-check correction `20261022_0028`). The App saves its random operation key before uploading,
polls `GET /v1/cry-analyses/by-key/{key}` or `GET /v1/cry-analyses/{id}` and
shows `done`, `incomplete` or `failed` without ever persisting WAV bytes.
For a committed Agent thread the App sends `thread_id` on upload; owner-scoped
`GET /v1/cry-analyses?thread_id=...` restores the latest 100 result cards on
another device. New jobs are limited to two per user and eight per regional
database while pending; retries with the same idempotency key do not claim a
new slot. The provider adapter sends one second of PCM at a time, pacing
prerecorded audio and restarting a closed no-cry activation for later blocks.
The classification runs in a bounded in-process background task, outside the
request DB transaction. A process restart can lose an in-flight recording;
after five minutes the pending record becomes `failed` and the user can rerecord.
This is not a durable audio queue; do not claim guaranteed completion across
host restarts. Private regional Product envs require
`ZOUNDREAM_ENDPOINT` and `ZOUNDREAM_API_KEY`. Agent Runtime has no vendor key
and receives only a completed owner/thread-bound result through authenticated internal
`POST /v1/internal/agent/cry-analyses/resolve`. Never attach WAV to the cry
Agent Run or claim live inference from API tests. Roll out Product migration
and private configuration before Agent/App; the North America same-schema
update runner does not permit this migration without a separately reviewed
schema-change procedure. Verify vendor reachability, endpoint semantics,
retention/privacy approval and real device flows per region before distribution.

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
