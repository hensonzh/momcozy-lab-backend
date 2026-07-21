from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    uri: str
    size_bytes: int
    content_type: str


class ObjectStorage(Protocol):
    async def put_bytes(self, *, key: str, body: bytes, content_type: str) -> StoredObject:
        raise NotImplementedError

    async def get_bytes(self, *, key: str) -> bytes:
        raise NotImplementedError

    async def get_byte_range(self, *, key: str, start: int, end: int) -> bytes:
        raise NotImplementedError

    async def create_presigned_get_url(self, *, key: str, expires_in_seconds: int) -> str:
        raise NotImplementedError

    async def delete(self, *, key: str) -> None:
        raise NotImplementedError
