#!/usr/bin/env bash
# Synthetic two-bucket MinIO copy and isolated restore. Never touches B volumes.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

image=momcozy-us-east-uat-minio:9e49d5e7a648f00e
revision=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
[[ "$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" == "$revision" ]]
source_name="momcozy-b-minio-source-check-$$"
restore_name="momcozy-b-minio-restore-check-$$"
backup_dir="$(mktemp -d)"
chmod 0700 "$backup_dir"
cleanup() {
  docker rm -f "$source_name" "$restore_name" >/dev/null 2>&1 || true
  rm -rf -- "$backup_dir"
}
trap cleanup EXIT

start_minio() {
  local name="$1"
  docker run -d --rm --name "$name" --network none --tmpfs /data:mode=0700 \
    -e MINIO_ROOT_USER=synthetic_b_root \
    -e MINIO_ROOT_PASSWORD=synthetic_b_root_password \
    "$image" server /data --address :9000 >/dev/null
  for _ in {1..30}; do
    if docker exec "$name" curl -fsS http://127.0.0.1:9000/minio/health/ready >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Synthetic B MinIO did not become ready" >&2
  return 1
}

start_minio "$source_name"
docker exec "$source_name" sh -ec '
  export MC_CONFIG_DIR=/tmp/momcozy-b-mc
  export MC_HOST_b="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000"
  mc mb b/momcozy-product-us-east-uat b/momcozy-agent-us-east-uat >/dev/null
  printf "synthetic product object\n" >/tmp/product-canary
  printf "synthetic agent object\n" >/tmp/agent-canary
  mc cp /tmp/product-canary b/momcozy-product-us-east-uat/recovery-canary >/dev/null
  mc cp /tmp/agent-canary b/momcozy-agent-us-east-uat/recovery-canary >/dev/null
  mkdir -p /tmp/momcozy-b-backup/product /tmp/momcozy-b-backup/agent
  mc mirror b/momcozy-product-us-east-uat /tmp/momcozy-b-backup/product >/dev/null
  mc mirror b/momcozy-agent-us-east-uat /tmp/momcozy-b-backup/agent >/dev/null
'
docker exec "$source_name" tar -C /tmp/momcozy-b-backup -cf - . | tar -C "$backup_dir" -xf -
[[ -f "$backup_dir/product/recovery-canary" && -f "$backup_dir/agent/recovery-canary" ]]
[[ "$(stat -f %Lp "$backup_dir" 2>/dev/null || stat -c %a "$backup_dir")" == 700 ]]

# The destination is a different server with no network, no named volume and
# no public host port. A read-only bind of the synthetic copy is its sole input.
docker run -d --rm --name "$restore_name" --network none --tmpfs /data:mode=0700 \
  -v "$backup_dir:/run/backup:ro" \
  -e MINIO_ROOT_USER=synthetic_b_root \
  -e MINIO_ROOT_PASSWORD=synthetic_b_root_password \
  "$image" server /data --address :9000 >/dev/null
for _ in {1..30}; do
  if docker exec "$restore_name" curl -fsS http://127.0.0.1:9000/minio/health/ready >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$restore_name" sh -ec '
  export MC_CONFIG_DIR=/tmp/momcozy-b-mc
  export MC_HOST_b="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000"
  mc mb b/momcozy-product-us-east-uat b/momcozy-agent-us-east-uat >/dev/null
  mc mirror /run/backup/product b/momcozy-product-us-east-uat >/dev/null
  mc mirror /run/backup/agent b/momcozy-agent-us-east-uat >/dev/null
'
for pair in 'product:momcozy-product-us-east-uat' 'agent:momcozy-agent-us-east-uat'; do
  kind="${pair%%:*}"
  bucket="${pair#*:}"
  expected="$(sha256sum "$backup_dir/$kind/recovery-canary" | cut -d' ' -f1)"
  actual="$(docker exec "$restore_name" sh -ec '
    export MC_CONFIG_DIR=/tmp/momcozy-b-mc
    export MC_HOST_b="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000"
    mc cat "b/$1/recovery-canary" | sha256sum | cut -d" " -f1
  ' sh "$bucket")"
  [[ "$expected" == "$actual" ]] || { echo "Synthetic B MinIO recovered object differs" >&2; exit 1; }
done
echo 'B synthetic MinIO two-bucket isolated restore passed (no host ports or persistent data)'
