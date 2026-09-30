# Product Backend — US East UAT (B lane)

This directory is a **template, not a runnable deployment**. A's root `Dockerfile`,
`docker-compose.deploy.yml`, `env/staging.env.example`, `scripts/release.py`,
and GitHub delivery workflow remain unchanged. B's release CLI still rejects
`north-america-staging`; do not disguise B as A's `staging` target.

## Build and deploy handoff for Codeup / IT

- Configure this repo's B Jenkins pipeline to build **Codeup `uat`**, as
  declared in `release-source.json`; A remains on its own `main` release path.
  IT says the B pipeline watches `uat`; confirm the job's exact branch filter.
  The `uat` branch is not yet present on Codeup, so it must be created and
  populated deliberately before the pipeline can fetch this configuration. The
  current files are local until deliberately committed and synchronized; a
  listener/port alone does not establish build or deployment readiness.
- Build from the **repository root**: `docker build -f deploy/Dockerfile -t <uat-registry>/<image>:<full-commit-sha> .`.
  Never use the root Dockerfile for B. Publish and deploy an immutable image **digest**;
  API, workers and migration Job for this repo use the *same digest*.
- `deploy/config_us-east-uat` is a non-secret `KEY=VALUE` seed for the B-only
  `momcozy-product-uat-config` ConfigMap; it does not contain working URLs or
  credentials. Do not inject A's private env, database, Redis or bucket. `APP_ENV`
  remains `staging` because the Python application has no `uat` mode.
- Before rendering manifests, IT must supply an **approved UAT namespace**,
  immutable image digest, UAT domain/TLS/Ingress and private role-scoped Secrets
  (`momcozy-product-api-uat-secrets`, `momcozy-product-notification-uat-secrets`,
  `momcozy-product-email-uat-secrets`, `momcozy-product-migrate-uat-secrets`).
  The migration secret needs DDL access; runtime identities should not.
  Secret data must be provided out-of-band, never committed or pasted into
  Jenkins logs. The ConfigMap must not contain secrets.
- All processes need their own required `DATABASE_URL` / `REDIS_URL` from a
  private Secret. Product DB is `momcozy_lab_pre` on the approved RDS endpoint;
  Redis uses the approved ElastiCache endpoint, port 6379, DB 0. Confirm actual
  ports, TLS and network access before composing URLs. Do **not** reuse the
  passwords shown in chat without IT security review and rotation decision.
  Set other application requirements (`TRUSTED_HOSTS`, JWT issuer/audiences and
  signing key, Product/Agent service identity, S3 bucket/credentials, public
  asset base URL, SMTP `AUTH_EMAIL_FROM` / `AUTH_SMTP_HOST` / `AUTH_SMTP_PORT`
  and password) with UAT-only values. Email worker must be able to reach the
  SMTP provider from the **UAT cluster**; domain verification alone is not a
  worker/mail delivery check.
- `workloads.yaml` has API, notification and email workers, plus an internal
  API Service. `migration-job.yaml` is a separate, per-release pre-deployment
  Job; replace the unique release ID and **wait for success before rollout**.
  Templates intentionally contain invalid `REPLACE_WITH_*` values and must not
  be applied as-is. No DB, Redis, bucket, Secret, namespace or Ingress is
  created by these manifests. Select the intended cluster/context explicitly;
  never point kubectl or Jenkins at A's cluster/namespace.
- Validate immutable image source and UAT Secret/ConfigMap names, run migrations,
  deploy, wait for `/v1/health/ready`, test email registration and reset on a
  synthetic account, inspect logs without secrets and prepare an independent
  rollback. Resource numbers for API/worker are initial references, not load
  test results; include rolling-update headroom in namespace quota.
