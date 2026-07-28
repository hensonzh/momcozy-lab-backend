# Product Object Storage Integration Profile

Local development uses MinIO to exercise the same S3-compatible semantics as
staging and production. Production selects a managed provider through
environment variables.

## Ownership Boundary

Object storage holds Product uploads and product-asset bytes. Postgres stores metadata,
owner scope, audit, and lifecycle state. Business code never
infers permission from an object key alone.

Agent Runtime does not receive Product object-storage credentials. For an
attachment, it calls `POST /v1/internal/agent/files/resolve` with its service
key, `actor_user_id`, stable Product `file_id`, and purpose. Product validates
ownership and returns a bounded signed URL. The signed URL is transport data,
not durable identity.

## CI Profile

The `object-storage-integration` check starts MinIO and runs:

```bash
OBJECT_STORAGE_PROVIDER=minio \
OBJECT_STORAGE_BUCKET=momcozy-test \
OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000 \
OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin \
OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin \
python scripts/check_object_storage_profile.py
```

The script ensures the bucket exists, writes a diagnostic object, reads it,
verifies the bytes, and deletes it.

## Local Profile

Local Docker Compose starts MinIO and creates the `momcozy-local` bucket before the
Product API starts:

```bash
make backend-local-up
```

The containerized API uses:

```env
OBJECT_STORAGE_PROVIDER=minio
OBJECT_STORAGE_BUCKET=momcozy-local
OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000
OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin
OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin
```

Host-side checks use the exposed localhost port:

```bash
set -a; . env/compose.local.env.example; set +a
python scripts/check_object_storage_profile.py
```

When Agent Runtime or a model provider must fetch a signed URL, configure
`OBJECT_STORAGE_PUBLIC_ENDPOINT_URL` as a stable HTTPS origin reachable from
that network. Never expose the MinIO administration endpoint or storage
credentials to Runtime or mobile clients.

Product validates owner/status/type on every internal file resolve and then
reuses the exact signed URL in Redis for `AGENT_FILE_URL_REUSE_TTL_SECONDS`.
Keep `AGENT_IMAGE_SIGNED_URL_TTL_SECONDS` at least five minutes longer than the
reuse window. Redis contains only reconstructable capability URLs; they must
not be persisted in PostgreSQL, Replay, request logs, or backups.
