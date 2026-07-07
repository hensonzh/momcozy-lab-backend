# Object Storage Integration Profile

The default local backend uses MinIO so development exercises the same
S3-compatible object storage semantics as staging and production. Production
uses a managed S3-compatible provider selected through environment variables.

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

Local Docker Compose starts MinIO and creates the `momcozy-local` bucket before
the API and workers start:

```bash
make backend-local-up
```

Containerized API and worker processes use the Compose service hostname:

```env
OBJECT_STORAGE_PROVIDER=minio \
OBJECT_STORAGE_BUCKET=momcozy-local \
OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000 \
OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin \
OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin
```

Host-side scripts use the same bucket through the exposed localhost port:

```bash
set -a; . production_backend/env/local.env.example; set +a
python production_backend/scripts/check_object_storage_profile.py
```
