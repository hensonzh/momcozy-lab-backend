from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import cast

from fastapi import Depends, Query, Request
from fastapi.responses import FileResponse, Response

from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.object_storage import ObjectStorage
from .models import ProductAsset
from .schemas import ProductAssetListResponse, ProductAssetRead
from .service import ProductAssetService


router = SurfaceAPIRouter(
    prefix="/assets",
    tags=["assets"],
    api_surface_metadata=api_surface("public_app_api", owner="product-assets", clients=["flutter"]),
)

_BYTE_RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")


def get_product_asset_service(request: Request) -> ProductAssetService:
    settings = request.app.state.settings
    local_root = str(settings.product_asset_local_root or "").strip()
    return ProductAssetService(
        manifest_path=Path(settings.product_asset_manifest_path),
        root=Path(local_root) if local_root else None,
    )


@router.get("", response_model=ProductAssetListResponse)
async def list_product_assets(
    limit: int = Query(default=100, ge=1, le=200),
    service: ProductAssetService = Depends(get_product_asset_service),
) -> ProductAssetListResponse:
    assets = service.list_assets(limit=limit)
    return ProductAssetListResponse(items=[_asset_read(asset) for asset in assets])


@router.get("/{asset_id}")
async def get_product_asset(
    asset_id: str,
    request: Request,
    service: ProductAssetService = Depends(get_product_asset_service),
) -> Response:
    asset = service.get_asset(asset_id=asset_id)
    return await _asset_response(asset=asset, request=request, include_body=True)


@router.head("/{asset_id}", include_in_schema=False)
async def head_product_asset(
    asset_id: str,
    request: Request,
    service: ProductAssetService = Depends(get_product_asset_service),
) -> Response:
    asset = service.get_asset(asset_id=asset_id)
    return await _asset_response(asset=asset, request=request, include_body=False)


def _asset_read(asset: ProductAsset) -> ProductAssetRead:
    return ProductAssetRead(
        id=asset.id,
        label=asset.label,
        domain=asset.domain,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
    )


async def _asset_response(
    *,
    asset: ProductAsset,
    request: Request,
    include_body: bool,
) -> Response:
    if asset.path is not None:
        return FileResponse(
            asset.path,
            media_type=asset.content_type,
            filename=asset.path.name,
            headers={"accept-ranges": "bytes"},
        )
    object_key = str(asset.object_key or "").strip()
    if not object_key:
        raise ApiError(code="asset_content_unavailable", message="Asset content is not configured.", status=503)
    if not include_body:
        return Response(
            status_code=200,
            media_type=asset.content_type,
            headers={
                "accept-ranges": "bytes",
                "content-length": str(asset.size_bytes),
            },
        )
    try:
        byte_range = _parse_byte_range(request.headers.get("range"), size=asset.size_bytes)
    except _RangeNotSatisfiable:
        return Response(
            status_code=416,
            media_type=asset.content_type,
            headers={
                "accept-ranges": "bytes",
                "content-range": f"bytes */{asset.size_bytes}",
                "content-length": "0",
            },
        )
    object_storage = _object_storage(request)
    try:
        if byte_range is None:
            body = await object_storage.get_bytes(key=object_key)
        else:
            body = await _read_object_byte_range(
                object_storage=object_storage,
                key=object_key,
                byte_range=byte_range,
            )
    except Exception as exc:
        raise ApiError(code="asset_content_unavailable", message="Asset content is unavailable.", status=503) from exc
    if byte_range is None:
        return Response(
            content=body,
            media_type=asset.content_type,
            headers={"accept-ranges": "bytes"},
        )
    if len(body) != byte_range.length:
        raise ApiError(code="asset_content_unavailable", message="Asset content is unavailable.", status=503)
    return Response(
        content=body,
        status_code=206,
        media_type=asset.content_type,
        headers={
            "accept-ranges": "bytes",
            "content-range": f"bytes {byte_range.start}-{byte_range.end}/{asset.size_bytes}",
            "content-length": str(byte_range.length),
        },
    )


def _object_storage(request: Request) -> ObjectStorage:
    object_storage = getattr(request.app.state, "object_storage", None)
    if object_storage is None:
        raise ApiError(code="asset_content_unavailable", message="Asset content storage is not configured.", status=503)
    return cast(ObjectStorage, object_storage)


@dataclass(frozen=True)
class _ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


class _RangeNotSatisfiable(ValueError):
    pass


def _parse_byte_range(value: str | None, *, size: int) -> _ByteRange | None:
    if value is None or not value.strip():
        return None
    match = _BYTE_RANGE_PATTERN.fullmatch(value.strip())
    if match is None or size <= 0:
        raise _RangeNotSatisfiable
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise _RangeNotSatisfiable
    if start_text:
        start = int(start_text)
        if start >= size:
            raise _RangeNotSatisfiable
        end = min(int(end_text), size - 1) if end_text else size - 1
        if end < start:
            raise _RangeNotSatisfiable
        return _ByteRange(start=start, end=end)
    suffix_length = int(end_text)
    if suffix_length <= 0:
        raise _RangeNotSatisfiable
    return _ByteRange(start=max(0, size - suffix_length), end=size - 1)


async def _read_object_byte_range(
    *,
    object_storage: ObjectStorage,
    key: str,
    byte_range: _ByteRange,
) -> bytes:
    reader = getattr(object_storage, "get_byte_range", None)
    if callable(reader):
        return cast(bytes, await reader(key=key, start=byte_range.start, end=byte_range.end))
    body = await object_storage.get_bytes(key=key)
    return body[byte_range.start : byte_range.end + 1]
