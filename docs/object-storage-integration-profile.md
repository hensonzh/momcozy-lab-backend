# Product Object Storage Integration Profile

Local development uses MinIO to exercise the same S3-compatible semantics as
test and production. Production selects a managed provider through
environment variables.

## Ownership Boundary

Object storage holds Product uploads and product-asset bytes. Postgres stores metadata,
owner scope, audit, and lifecycle state. Business code never
infers permission from an object key alone.

Agent Runtime does not receive Product object-storage credentials. For an
attachment, it calls `POST /v1/internal/agent/files/resolve` with its service
key, `actor_user_id`, stable Product `file_id`, and purpose. Product validates
ownership and returns an opaque Product capability URL. The URL is transport
data, not durable identity.

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
Product Backend API starts:

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

When Agent Runtime supplies model attachments, configure
`AGENT_MODEL_ASSET_PUBLIC_BASE_URL` as the public HTTPS Product Backend API origin
reachable by the model provider. Never expose the MinIO administration
endpoint or storage credentials to Runtime or mobile clients.

Product validates owner/status/type on every internal file resolve, issues one
opaque Redis-backed capability, and renews its 30-minute inactivity TTL only
on authorized resolves. The public fetch route revalidates the file and
proxies bytes from object storage without renewing TTL. Capability URLs must
not be persisted in PostgreSQL or Replay, and path tokens are redacted from
application logs.
