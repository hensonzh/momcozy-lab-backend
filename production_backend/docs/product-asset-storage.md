# Product Asset Storage

Large product assets such as device guidance images, videos, and PDFs should not
be packaged into API or worker images. The backend keeps only a small manifest
in git and reads asset bytes from object storage.

## Runtime Model

```text
product-assets.manifest.json
  -> asset id, label, content type, size, object_key
  -> ObjectStorage provider
  -> local filesystem / MinIO / S3 / OSS / COS
```

Postgres is not the authority for product asset bytes. Object storage stores the
content, while the manifest controls the public allowlist exposed by
`/v1/assets`.

## Build Manifest

Given a local source directory before upload:

```bash
python production_backend/scripts/build_product_asset_manifest.py \
  --source-root /path/to/device-guidance/assets \
  --output production_backend/assets/product-assets.manifest.json \
  --object-key-prefix product-assets/device-guidance/assets
```

Upload the same directory to the configured object storage prefix. The generated
`object_key` values must match the upload destination.

Verify the manifest and storage contents after upload:

```bash
python production_backend/scripts/check_product_asset_storage.py
```

The check validates that every manifest `object_key` exists in the configured
object storage provider and that the stored byte size matches `size_bytes`.

## Local Development

The default local profile uses filesystem object storage:

```env
OBJECT_STORAGE_PROVIDER=local
OBJECT_STORAGE_LOCAL_ROOT=production_backend/.local/object_storage
PRODUCT_ASSET_MANIFEST_PATH=production_backend/assets/product-assets.manifest.json
```

For local development, copy the product asset files into the matching object
storage prefix under `.local/object_storage`:

```text
production_backend/.local/object_storage/product-assets/...
```

The `.local/` directory is ignored by git, so large product asset blobs stay out
of the repository and worker images.

To exercise the full object-storage path locally, start MinIO:

```bash
make backend-local-minio
OBJECT_STORAGE_PROVIDER=minio \
OBJECT_STORAGE_BUCKET=momcozy-test \
OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000 \
OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin \
OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin \
python production_backend/scripts/check_object_storage_profile.py
```

## Worker Boundary

Agent and outbox workers receive the same `ObjectStorage` provider as the API
process, but they do not need product asset blobs on disk. This keeps worker
images small and makes asset rollout an object-storage operation instead of an
application redeploy.
