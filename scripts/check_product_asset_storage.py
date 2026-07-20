from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if TYPE_CHECKING:
    from app.infrastructure.object_storage import ObjectStorage
    from app.modules.assets.models import ProductAsset


@dataclass(frozen=True)
class AssetStorageCheckResult:
    total: int
    checked: int
    missing: tuple[str, ...]
    size_mismatches: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.size_mismatches

    def to_payload(self) -> dict[str, object]:
        return {
            "status": "pass" if self.ok else "fail",
            "total": self.total,
            "checked": self.checked,
            "missing": list(self.missing),
            "size_mismatches": list(self.size_mismatches),
        }


async def run_check() -> AssetStorageCheckResult:
    from app.core.settings import Settings
    from app.infrastructure.object_storage import create_object_storage
    from app.modules.assets.service import ProductAssetService

    settings = Settings.from_env()
    storage = create_object_storage(settings)
    service = ProductAssetService(manifest_path=Path(settings.product_asset_manifest_path))
    assets = service.list_assets(limit=200)
    return await check_manifest_assets(storage=storage, assets=assets)


async def check_manifest_assets(
    *,
    storage: "ObjectStorage",
    assets: list["ProductAsset"],
) -> AssetStorageCheckResult:
    missing: list[str] = []
    size_mismatches: list[str] = []
    checked = 0

    for asset in assets:
        object_key = str(asset.object_key or "").strip()
        if not object_key:
            missing.append(f"{asset.id}: missing object_key")
            continue
        checked += 1
        try:
            actual_size = await _stored_size(storage=storage, key=object_key)
        except FileNotFoundError:
            missing.append(object_key)
            continue
        except Exception as exc:  # pragma: no cover - provider-specific diagnostics
            missing.append(f"{object_key}: {type(exc).__name__}: {exc}")
            continue
        if actual_size != asset.size_bytes:
            size_mismatches.append(f"{object_key}: expected {asset.size_bytes}, got {actual_size}")

    return AssetStorageCheckResult(
        total=len(assets),
        checked=checked,
        missing=tuple(missing),
        size_mismatches=tuple(size_mismatches),
    )


async def _stored_size(*, storage: "ObjectStorage", key: str) -> int:
    from botocore.exceptions import ClientError

    from app.infrastructure.object_storage import LocalObjectStorage, S3ObjectStorage

    if isinstance(storage, LocalObjectStorage):
        path = storage._path_for_key(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return path.stat().st_size

    if isinstance(storage, S3ObjectStorage):
        try:
            response = await asyncio.to_thread(storage.client.head_object, Bucket=storage.bucket, Key=key)
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(key) from exc
            raise
        return cast(int, response.get("ContentLength", 0))

    body = await storage.get_bytes(key=key)
    return len(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check every product asset manifest object exists in object storage.")
    parser.parse_args()
    result = asyncio.run(run_check())
    print(json.dumps(result.to_payload(), indent=2, sort_keys=True))
    if not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
