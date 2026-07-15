from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

from .base import StoredObject


class LocalObjectStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def put_bytes(self, *, key: str, body: bytes, content_type: str) -> StoredObject:
        path = self._path_for_key(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, body)
        normalized_key = self._normalize_key(key)
        return StoredObject(
            key=normalized_key,
            uri=f"local://{normalized_key}",
            size_bytes=len(body),
            content_type=content_type,
        )

    async def get_bytes(self, *, key: str) -> bytes:
        path = self._path_for_key(key)
        return await asyncio.to_thread(path.read_bytes)

    async def get_byte_range(self, *, key: str, start: int, end: int) -> bytes:
        path = self._path_for_key(key)

        def _read_range() -> bytes:
            with path.open("rb") as handle:
                handle.seek(start)
                return handle.read(end - start + 1)

        return await asyncio.to_thread(_read_range)

    async def delete(self, *, key: str) -> None:
        path = self._path_for_key(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)

    def _path_for_key(self, key: str) -> Path:
        normalized_key = self._normalize_key(key)
        return self.root.joinpath(*PurePosixPath(normalized_key).parts)

    @staticmethod
    def _normalize_key(key: str) -> str:
        normalized = PurePosixPath(str(key or "").strip())
        if not normalized.parts or str(normalized) in {"", "."}:
            raise ValueError("object key is required")
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError("object key must be relative and cannot contain '..'")
        return str(normalized)
