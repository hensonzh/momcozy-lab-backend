#!/usr/bin/env bash
set -Eeuo pipefail

: "${MOMCOZY_STAGING_PRODUCT_POSTGRES_PASSWORD:?required}"
: "${MOMCOZY_STAGING_AGENT_POSTGRES_PASSWORD:?required}"

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=product_password="$MOMCOZY_STAGING_PRODUCT_POSTGRES_PASSWORD" \
  --set=agent_password="$MOMCOZY_STAGING_AGENT_POSTGRES_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L',
  'momcozy_staging',
  :'product_password'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'momcozy_staging'
)
\gexec

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L',
  'agent_runtime_staging',
  :'agent_password'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'agent_runtime_staging'
)
\gexec

SELECT format(
  'CREATE DATABASE %I OWNER %I',
  'momcozy_staging',
  'momcozy_staging'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = 'momcozy_staging'
)
\gexec

SELECT format(
  'CREATE DATABASE %I OWNER %I',
  'agent_runtime_staging',
  'agent_runtime_staging'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = 'agent_runtime_staging'
)
\gexec
SQL
