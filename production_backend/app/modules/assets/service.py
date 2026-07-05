from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...core.errors import ApiError
from .models import ProductAsset


PRODUCTION_BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRODUCT_ASSET_MANIFEST_PATH = (PRODUCTION_BACKEND_ROOT / "assets" / "product-assets.manifest.json").resolve()
SUPPORTED_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".webp": "image/webp",
}


class ProductAssetService:
    def __init__(
        self,
        *,
        manifest_path: Path = DEFAULT_PRODUCT_ASSET_MANIFEST_PATH,
        root: Path | None = None,
    ) -> None:
        self.manifest_path = manifest_path.resolve()
        self.root = root.resolve() if root is not None else None

    def list_assets(self, *, limit: int = 100) -> list[ProductAsset]:
        if limit < 1 or limit > 200:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 200.", status=422)
        return self._manifest()[:limit]

    def get_asset(self, *, asset_id: str) -> ProductAsset:
        normalized_id = str(asset_id or "").strip()
        if not normalized_id:
            raise ApiError(code="not_found", message="Asset not found.", status=404)
        for asset in self._manifest():
            if asset.id == normalized_id:
                return asset
        raise ApiError(code="not_found", message="Asset not found.", status=404)

    def _manifest(self) -> list[ProductAsset]:
        if self.root is not None:
            return self._local_root_manifest()
        if self.manifest_path.exists():
            return _load_manifest(self.manifest_path)
        return []

    def _local_root_manifest(self) -> list[ProductAsset]:
        if self.root is None or not self.root.exists():
            return []
        assets: list[ProductAsset] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            content_type = _content_type(path)
            if not content_type:
                continue
            relative_path = path.relative_to(self.root).as_posix()
            assets.append(
                ProductAsset(
                    id=_asset_id(relative_path),
                    label=path.stem.replace("_", " ").replace("-", " ").strip(),
                    domain="device_guidance",
                    content_type=content_type,
                    size_bytes=path.stat().st_size,
                    path=path,
                )
            )
        return assets


def _load_manifest(path: Path) -> list[ProductAsset]:
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    raw_assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(raw_assets, list):
        raise ApiError(code="asset_manifest_invalid", message="Product asset manifest is invalid.", status=500)

    assets: list[ProductAsset] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            continue
        asset = _asset_from_manifest(raw_asset)
        if asset is not None:
            assets.append(asset)
    return assets


def _asset_from_manifest(raw_asset: dict[str, Any]) -> ProductAsset | None:
    asset_id = _manifest_text(raw_asset, "id")
    label = _manifest_text(raw_asset, "label")
    domain = _manifest_text(raw_asset, "domain")
    content_type = _manifest_text(raw_asset, "content_type")
    object_key = _manifest_text(raw_asset, "object_key")
    size_bytes = raw_asset.get("size_bytes")
    if not asset_id or not label or not domain or not content_type or not object_key:
        return None
    if not isinstance(size_bytes, int) or size_bytes < 0:
        return None
    if content_type not in SUPPORTED_CONTENT_TYPES.values():
        return None
    return ProductAsset(
        id=asset_id,
        label=label,
        domain=domain,
        content_type=content_type,
        size_bytes=size_bytes,
        object_key=object_key,
    )


def _manifest_text(raw_asset: dict[str, Any], key: str) -> str:
    value = raw_asset.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip()


def _asset_id(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]
    return f"asset_{digest}"


def _content_type(path: Path) -> str:
    return SUPPORTED_CONTENT_TYPES.get(path.suffix.lower(), "")
