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

    async def delete(self, *, key: str) -> None:
        raise NotImplementedError
