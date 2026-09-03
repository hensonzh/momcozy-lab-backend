#!/usr/bin/env sh
set -eu

: "${MOMCOZY_TEST_REDIS_ADMIN_PASSWORD:?required}"
: "${MOMCOZY_TEST_PRODUCT_REDIS_PASSWORD:?required}"
: "${MOMCOZY_TEST_AGENT_REDIS_PASSWORD:?required}"

umask 077
acl_file=/run/redis/users.acl
{
  printf 'user default off\n'
  printf 'user test-admin on >%s ~* &* +@all\n' "$MOMCOZY_TEST_REDIS_ADMIN_PASSWORD"
  printf 'user product-backend on >%s ~rate-limit:* ~product:agent-asset-capability:v1:* &rate-limit:* &product:* +@all -@dangerous\n' "$MOMCOZY_TEST_PRODUCT_REDIS_PASSWORD"
  printf 'user agent-runtime on >%s ~agent-runtime:* ~momcozy-agent-runtime:* &agent-runtime:* &momcozy-agent-runtime:* +@all -@dangerous\n' "$MOMCOZY_TEST_AGENT_REDIS_PASSWORD"
} > "$acl_file"

exec redis-server --appendonly yes --aclfile "$acl_file"
