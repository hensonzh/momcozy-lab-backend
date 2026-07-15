from __future__ import annotations

from pathlib import Path

from ...core.settings import Settings
from .base import ObjectStorage
from .local import LocalObjectStorage
from .s3 import S3ObjectStorage


def create_object_storage(settings: Settings) -> ObjectStorage:
    provider = settings.object_storage_provider.lower()
    if provider == "local":
        return LocalObjectStorage(Path(settings.object_storage_local_root))
    if provider in {"s3", "minio", "oss", "cos"}:
        return S3ObjectStorage(
            bucket=settings.object_storage_bucket,
            region=settings.object_storage_region,
            endpoint_url=settings.object_storage_endpoint_url,
            access_key_id=settings.object_storage_access_key_id,
            secret_access_key=settings.object_storage_secret_access_key,
        )

    raise ValueError(f"unsupported object storage provider: {provider}")
