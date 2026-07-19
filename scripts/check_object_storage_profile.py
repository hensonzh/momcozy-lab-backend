from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if TYPE_CHECKING:
    from app.infrastructure.object_storage import S3ObjectStorage


async def run_check() -> dict[str, object]:
    from app.core.settings import Settings
    from app.infrastructure.object_storage import S3ObjectStorage, create_object_storage

    settings = Settings.from_env()
    storage = create_object_storage(settings)
    key = f"diagnostics/object-storage/{uuid4().hex}.txt"
    body = b"momcozy-object-storage-check"
    wrote_object = False

    if isinstance(storage, S3ObjectStorage):
        await asyncio.to_thread(_ensure_s3_bucket, storage)

    try:
        stored = await storage.put_bytes(key=key, body=body, content_type="text/plain")
        wrote_object = True
        loaded = await storage.get_bytes(key=key)
        if loaded != body:
            raise RuntimeError("object storage round-trip body mismatch")
        return {
            "provider": settings.object_storage_provider,
            "bucket_configured": bool(settings.object_storage_bucket),
            "checked": ["put_bytes", "get_bytes", "delete"],
            "stored_key": stored.key,
            "stored_uri_scheme": stored.uri.split(":", 1)[0],
        }
    finally:
        if wrote_object:
            await storage.delete(key=key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check object storage provider put/get/delete semantics.")
    parser.parse_args()
    print(json.dumps(asyncio.run(run_check()), indent=2, sort_keys=True))


def _ensure_s3_bucket(storage: S3ObjectStorage) -> None:
    try:
        storage.client.head_bucket(Bucket=storage.bucket)
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        storage.client.create_bucket(Bucket=storage.bucket)


if __name__ == "__main__":
    main()
