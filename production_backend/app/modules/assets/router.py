from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response

from ...core.errors import ApiError
from ...infrastructure.object_storage import ObjectStorage
from .models import ProductAsset
from .schemas import ProductAssetListResponse, ProductAssetRead
from .service import ProductAssetService


router = APIRouter(prefix="/assets", tags=["assets"])


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
        return FileResponse(asset.path, media_type=asset.content_type, filename=asset.path.name)
    object_key = str(asset.object_key or "").strip()
    if not object_key:
        raise ApiError(code="asset_content_unavailable", message="Asset content is not configured.", status=503)
    if not include_body:
        return Response(
            status_code=200,
            media_type=asset.content_type,
            headers={"content-length": str(asset.size_bytes)},
        )
    object_storage = _object_storage(request)
    try:
        body = await object_storage.get_bytes(key=object_key)
    except Exception as exc:
        raise ApiError(code="asset_content_unavailable", message="Asset content is unavailable.", status=503) from exc
    return Response(content=body, media_type=asset.content_type)


def _object_storage(request: Request) -> ObjectStorage:
    object_storage = getattr(request.app.state, "object_storage", None)
    if object_storage is None:
        raise ApiError(code="asset_content_unavailable", message="Asset content storage is not configured.", status=503)
    return cast(ObjectStorage, object_storage)
