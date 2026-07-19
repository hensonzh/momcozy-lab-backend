from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any


SUPPORTED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
    "video/mp4",
}


def build_manifest(*, source_root: Path, object_key_prefix: str) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    prefix = object_key_prefix.strip().strip("/")
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        content_type = mimetypes.guess_type(path.name)[0] or ""
        if content_type not in SUPPORTED_CONTENT_TYPES:
            continue
        relative_path = path.relative_to(source_root).as_posix()
        object_key = f"{prefix}/{relative_path}" if prefix else relative_path
        assets.append(
            {
                "id": _asset_id(relative_path),
                "label": path.stem.replace("_", " ").replace("-", " ").strip(),
                "domain": "device_guidance",
                "content_type": content_type,
                "size_bytes": path.stat().st_size,
                "object_key": object_key,
            }
        )
    return {"schema_version": "product_assets.v1", "assets": assets}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a product asset manifest for OSS-backed assets.")
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="Directory containing source asset files before they are uploaded to object storage.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Manifest JSON path to write.",
    )
    parser.add_argument(
        "--object-key-prefix",
        default="product-assets/device-guidance/assets",
        help="Object storage key prefix matching the upload destination.",
    )
    args = parser.parse_args()

    manifest = build_manifest(source_root=args.source_root, object_key_prefix=args.object_key_prefix)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _asset_id(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]
    return f"asset_{digest}"


if __name__ == "__main__":
    main()
