#!/usr/bin/env bash
set -Eeuo pipefail

for name in \
  MOMCOZY_PRODUCT_POSTGRES_DB \
  MOMCOZY_PRODUCT_POSTGRES_USER \
  MOMCOZY_PRODUCT_POSTGRES_PASSWORD \
  MOMCOZY_AGENT_POSTGRES_DB \
  MOMCOZY_AGENT_POSTGRES_USER \
  MOMCOZY_AGENT_POSTGRES_PASSWORD; do
  : "${!name:?required}"
done

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=product_db="$MOMCOZY_PRODUCT_POSTGRES_DB" \
  --set=product_user="$MOMCOZY_PRODUCT_POSTGRES_USER" \
  --set=product_password="$MOMCOZY_PRODUCT_POSTGRES_PASSWORD" \
  --set=agent_db="$MOMCOZY_AGENT_POSTGRES_DB" \
  --set=agent_user="$MOMCOZY_AGENT_POSTGRES_USER" \
  --set=agent_password="$MOMCOZY_AGENT_POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'product_user', :'product_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = :'product_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'agent_user', :'agent_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = :'agent_user')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'product_db', :'product_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'product_db')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'agent_db', :'agent_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'agent_db')
\gexec
-- PostgreSQL defaults grant CONNECT on every database to PUBLIC. Restrict both
-- B databases to the owning service role so separate DBs are a real boundary.
SELECT format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', :'product_db')
\gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'product_db', :'product_user')
\gexec
SELECT format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', :'agent_db')
\gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'agent_db', :'agent_user')
\gexec
SQL
