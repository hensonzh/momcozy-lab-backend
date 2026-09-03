#!/usr/bin/env bash
set -Eeuo pipefail

: "${MOMCOZY_TEST_PRODUCT_POSTGRES_PASSWORD:?required}"
: "${MOMCOZY_TEST_AGENT_POSTGRES_PASSWORD:?required}"

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=product_password="$MOMCOZY_TEST_PRODUCT_POSTGRES_PASSWORD" \
  --set=agent_password="$MOMCOZY_TEST_AGENT_POSTGRES_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L',
  'momcozy_test',
  :'product_password'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'momcozy_test'
)
\gexec

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L',
  'agent_runtime_test',
  :'agent_password'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'agent_runtime_test'
)
\gexec

SELECT format(
  'CREATE DATABASE %I OWNER %I',
  'momcozy_test',
  'momcozy_test'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = 'momcozy_test'
)
\gexec

SELECT format(
  'CREATE DATABASE %I OWNER %I',
  'agent_runtime_test',
  'agent_runtime_test'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = 'agent_runtime_test'
)
\gexec
SQL
