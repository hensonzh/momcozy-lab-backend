# Object Storage Integration Profile

The default local backend uses filesystem-backed object storage. Production uses
a managed S3-compatible provider selected through environment variables.

## Runtime Boundary

Object storage stores large files and agent artifacts. Postgres stores metadata,
owner scope, audit, and lifecycle. Business code should never infer permission
or ownership from object keys alone.

## CI Profile

The `object-storage-integration` CI job starts a MinIO container and runs:

```bash
OBJECT_STORAGE_PROVIDER=minio \
OBJECT_STORAGE_BUCKET=momcozy-test \
OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000 \
OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin \
OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin \
python production_backend/scripts/check_object_storage_profile.py
```

The script ensures the bucket exists, writes a diagnostic object, reads it back,
verifies the body, and deletes the generated object.

## Local Profile

When Docker is available:

```bash
docker compose -f production_backend/docker-compose.yml --profile tools up -d minio
OBJECT_STORAGE_PROVIDER=minio \
OBJECT_STORAGE_BUCKET=momcozy-test \
OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000 \
OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin \
OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin \
  python production_backend/scripts/check_object_storage_profile.py
```

This live profile is separate from the fast unit/contract suite so normal
development does not require local infrastructure.
