from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from .models import ProductAsset
from .schemas import ProductAssetListResponse, ProductAssetRead
from .service import ProductAssetService


router = APIRouter(prefix="/assets", tags=["assets"])


def get_product_asset_service() -> ProductAssetService:
    return ProductAssetService()


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
    service: ProductAssetService = Depends(get_product_asset_service),
) -> FileResponse:
    asset = service.get_asset(asset_id=asset_id)
    return FileResponse(asset.path, media_type=asset.content_type, filename=asset.path.name)


@router.head("/{asset_id}", include_in_schema=False)
async def head_product_asset(
    asset_id: str,
    service: ProductAssetService = Depends(get_product_asset_service),
) -> FileResponse:
    return await get_product_asset(asset_id=asset_id, service=service)


def _asset_read(asset: ProductAsset) -> ProductAssetRead:
    return ProductAssetRead(
        id=asset.id,
        label=asset.label,
        domain=asset.domain,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
    )
