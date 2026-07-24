from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_SOURCE_PATH = ROOT / "assets" / "agent-references" / "pump-models.md"

if TYPE_CHECKING:
    from app.infrastructure.object_storage import ObjectStorage, StoredObject


async def publish_pump_models_reference(
    *,
    storage: ObjectStorage,
    source_path: Path,
) -> StoredObject:
    from app.agents.cozymate.tools.pump_models import (
        MAX_PUMP_MODELS_DOCUMENT_BYTES,
        PUMP_MODELS_OBJECT_KEY,
        parse_pump_models_markdown,
    )

    body = source_path.read_bytes()
    if len(body) > MAX_PUMP_MODELS_DOCUMENT_BYTES:
        raise ValueError("pump models reference exceeds the size limit")
    markdown = body.decode("utf-8")
    parse_pump_models_markdown(markdown)
    return await storage.put_bytes(
        key=PUMP_MODELS_OBJECT_KEY,
        body=body,
        content_type="text/markdown; charset=utf-8",
    )


async def _run(*, source_path: Path) -> StoredObject:
    from app.core.settings import Settings
    from app.infrastructure.object_storage import create_object_storage

    settings = Settings.from_env()
    storage = create_object_storage(settings)
    return await publish_pump_models_reference(storage=storage, source_path=source_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and publish the pump model Markdown reference to object storage.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE_PATH,
        help="Path to the pump model Markdown publication source.",
    )
    args = parser.parse_args()
    stored = asyncio.run(_run(source_path=args.source.resolve()))
    print(
        json.dumps(
            {
                "status": "published",
                "object_key": stored.key,
                "size_bytes": stored.size_bytes,
                "content_type": stored.content_type,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
