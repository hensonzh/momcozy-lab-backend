from __future__ import annotations

from pydantic import BaseModel


class ProductAssetRead(BaseModel):
    id: str
    label: str
    domain: str
    content_type: str
    size_bytes: int


class ProductAssetListResponse(BaseModel):
    items: list[ProductAssetRead]
    next_cursor: str | None = None
