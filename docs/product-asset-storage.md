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
python scripts/build_product_asset_manifest.py \
  --source-root /path/to/device-guidance/assets \
  --output assets/product-assets.manifest.json \
  --object-key-prefix product-assets/device-guidance/assets
```

Upload the same directory to the configured object storage prefix. The generated
`object_key` values must match the upload destination.

Verify the manifest and storage contents after upload:

```bash
python scripts/check_product_asset_storage.py
```

The check validates that every manifest `object_key` exists in the configured
object storage provider and that the stored byte size matches `size_bytes`.

## Local Development

The default local profile uses MinIO object storage:

```env
OBJECT_STORAGE_PROVIDER=minio
OBJECT_STORAGE_BUCKET=momcozy-local
OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000
OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin
OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin
PRODUCT_ASSET_MANIFEST_PATH=assets/product-assets.manifest.json
```

For local development, upload the product asset files into the matching object
storage prefix in the `momcozy-local` MinIO bucket:

```text
momcozy-local/product-assets/...
```

Start MinIO through Compose before running storage checks:

```bash
make backend-local-minio
set -a; . env/compose.local.env.example; set +a
python scripts/check_object_storage_profile.py
```

Large product asset blobs still stay out of the repository and worker images;
the manifest is committed, while bytes are published to object storage.

## Worker Boundary

Agent and outbox workers receive the same `ObjectStorage` provider as the API
process, but they do not need product asset blobs on disk. This keeps worker
images small and makes asset rollout an object-storage operation instead of an
application redeploy.
