from __future__ import annotations

from pathlib import Path

from ...core.settings import Settings
from .base import ObjectStorage
from .local import LocalObjectStorage


def create_object_storage(settings: Settings) -> ObjectStorage:
    provider = settings.object_storage_provider.lower()
    if provider == "local":
        return LocalObjectStorage(Path(settings.object_storage_local_root))

    raise NotImplementedError(f"object storage provider is not implemented: {provider}")
