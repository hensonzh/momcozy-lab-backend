from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if TYPE_CHECKING:
    from app.infrastructure.object_storage import ObjectStorage


async def check_pump_models_reference(*, storage: ObjectStorage) -> dict[str, object]:
    from app.agents.cozymate.tools.pump_models import (
        PUMP_MODELS_OBJECT_KEY,
        PumpModelsReferenceService,
    )

    reference = await PumpModelsReferenceService(object_storage=storage).read()
    products = reference["products"]
    return {
        "status": "pass",
        "object_key": PUMP_MODELS_OBJECT_KEY,
        "schema_version": reference["schema_version"],
        "currency": reference["currency"],
        "product_count": len(products) if isinstance(products, list) else 0,
    }


async def _run() -> dict[str, object]:
    from app.core.settings import Settings
    from app.infrastructure.object_storage import create_object_storage

    settings = Settings.from_env()
    storage = create_object_storage(settings)
    return await check_pump_models_reference(storage=storage)


def main() -> None:
    print(json.dumps(asyncio.run(_run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
