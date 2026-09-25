# Product Asset Storage

Large device-guidance images, videos, and PDFs are not packaged into the Product
API image. Git stores a small allowlist manifest; object storage stores bytes.

## Storage Model

```text
product-assets.manifest.json
  -> asset id, label, content type, size, object_key
  -> Product ObjectStorage provider
  -> MinIO / S3 / OSS / COS
```

The manifest controls the allowlist exposed by `GET /v1/assets` and
`GET /v1/assets/{asset_id}`. Clients use asset ids and never construct URLs from
filesystem paths or object keys.

## Build And Verify

Build a manifest from the publication source:

```bash
python scripts/build_product_asset_manifest.py \
  --source-root /path/to/device-guidance/assets \
  --output assets/product-assets.manifest.json \
  --object-key-prefix product-assets/device-guidance/assets
```

Upload the same directory to the configured prefix, then verify:

```bash
python scripts/check_product_asset_storage.py
```

The check confirms every allowlisted object exists and its stored byte size
matches `size_bytes`.

## Local Development

The local profile uses the `momcozy-local` MinIO bucket:

```env
OBJECT_STORAGE_PROVIDER=minio
OBJECT_STORAGE_BUCKET=momcozy-local
OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000
OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin
OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin
PRODUCT_ASSET_MANIFEST_PATH=assets/product-assets.manifest.json
```

Publish asset bytes under:

```text
momcozy-local/product-assets/...
```

Run storage checks after MinIO starts:

```bash
make backend-local-minio
set -a; . env/local.env.example; set +a
python scripts/check_object_storage_profile.py
python scripts/check_product_asset_storage.py
```

Only the manifest belongs in the application repository and image. Runtime
reference documents, model knowledge, and specialist content are owned and
published from the Agent Runtime repository; they are not Product assets.
