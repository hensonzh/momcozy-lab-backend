#!/usr/bin/env bash
# Isolated synthetic B bootstrap smoke test. No host ports, private env, or deployment.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

postgres_name="momcozy-b-postgres-check-$$"
redis_name="momcozy-b-redis-check-$$"
cleanup() {
  docker rm -f "$postgres_name" "$redis_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --rm --name "$postgres_name" --network none \
  -e POSTGRES_USER=synthetic_admin -e POSTGRES_PASSWORD=synthetic_admin_password \
  -e POSTGRES_DB=postgres \
  -e MOMCOZY_PRODUCT_POSTGRES_DB=momcozy_lab_backend_uat \
  -e MOMCOZY_PRODUCT_POSTGRES_USER=momcozy_lab_backend_uat \
  -e MOMCOZY_PRODUCT_POSTGRES_PASSWORD=synthetic_product_password \
  -e MOMCOZY_AGENT_POSTGRES_DB=momcozy_lab_agent_uat \
  -e MOMCOZY_AGENT_POSTGRES_USER=momcozy_lab_agent_uat \
  -e MOMCOZY_AGENT_POSTGRES_PASSWORD=synthetic_agent_password \
  -v "$PWD/deploy/us-east-uat/init-postgres.sh:/docker-entrypoint-initdb.d/10-init-momcozy-databases.sh:ro" \
  postgres:16 >/dev/null

# Wait for the entrypoint's init script, not just pg_isready (which can become
# true before the database creation finishes).
for attempt in {1..60}; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$postgres_name")" != true ]]; then
    echo 'B PostgreSQL bootstrap exited early' >&2
    exit 1
  fi
  count="$(docker exec "$postgres_name" psql -U synthetic_admin -d postgres -Atqc \
    "SELECT count(*) FROM pg_database WHERE datname IN ('momcozy_lab_backend_uat','momcozy_lab_agent_uat')" 2>/dev/null || true)"
  [[ "$count" == 2 ]] && break
  sleep 1
done
[[ "$count" == 2 ]] || { echo 'B PostgreSQL databases were not initialized' >&2; exit 1; }
for name in momcozy_lab_backend_uat momcozy_lab_agent_uat; do
  [[ "$(docker exec "$postgres_name" psql -U "$name" -d "$name" -Atqc 'SELECT 1')" == 1 ]]
done
if docker exec "$postgres_name" psql -U momcozy_lab_backend_uat -d momcozy_lab_agent_uat -Atqc 'SELECT 1' >/dev/null 2>&1; then
  echo 'Product role unexpectedly connected to Agent database' >&2; exit 1
fi
if docker exec "$postgres_name" psql -U momcozy_lab_agent_uat -d momcozy_lab_backend_uat -Atqc 'SELECT 1' >/dev/null 2>&1; then
  echo 'Agent role unexpectedly connected to Product database' >&2; exit 1
fi

docker run -d --rm --name "$redis_name" --network none --tmpfs /run/redis:mode=0700 --tmpfs /data \
  -e MOMCOZY_REDIS_ADMIN_PASSWORD=synthetic_admin_password \
  -e MOMCOZY_PRODUCT_REDIS_PASSWORD=synthetic_product_password \
  -e MOMCOZY_AGENT_REDIS_PASSWORD=synthetic_agent_password \
  -v "$PWD/deploy/us-east-uat/start-redis.sh:/usr/local/bin/start-momcozy-redis:ro" \
  --entrypoint /bin/sh redis:7.4-alpine /usr/local/bin/start-momcozy-redis >/dev/null
for attempt in {1..30}; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$redis_name")" != true ]]; then
    echo 'B Redis ACL bootstrap exited early' >&2; exit 1
  fi
  if [[ "$(docker exec "$redis_name" redis-cli --user deployment-admin --pass synthetic_admin_password --no-auth-warning ping 2>/dev/null || true)" == PONG ]]; then
    break
  fi
  sleep 1
done
redis_as() {
  local user="$1"; shift
  local password=synthetic_product_password
  [[ "$user" == agent-runtime ]] && password=synthetic_agent_password
  docker exec "$redis_name" redis-cli --user "$user" --pass "$password" --no-auth-warning "$@"
}
[[ "$(redis_as product-backend set rate-limit:smoke ok)" == OK ]]
[[ "$(redis_as agent-runtime set agent-runtime:smoke ok)" == OK ]]
[[ "$(redis_as product-backend eval 'return redis.call("INCR", KEYS[1])' 1 rate-limit:lua-smoke)" == 1 ]]
[[ "$(redis_as agent-runtime xadd agent-runtime:stream-smoke '*' value ok)" == *-* ]]
for user in product-backend agent-runtime; do
  [[ "$(redis_as "$user" select 1)" == *NOPERM* ]] || { echo 'B Redis DB switch not denied' >&2; exit 1; }
done
[[ "$(redis_as product-backend get agent-runtime:smoke)" == *NOPERM* ]]
[[ "$(redis_as agent-runtime get rate-limit:smoke)" == *NOPERM* ]]
echo 'B synthetic PostgreSQL and Redis boundaries passed (no host ports or persistent data)'
