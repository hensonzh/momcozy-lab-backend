from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def run_check() -> dict[str, object]:
    from app.core.settings import Settings
    from app.infrastructure.db import create_db_engine

    settings = Settings.from_env()
    engine = create_db_engine(settings)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("select 1"))
            if result.scalar_one() != 1:
                raise RuntimeError("database select 1 returned an unexpected value")
        return {
            "database_url_configured": bool(settings.database_url),
            "checked": ["connect", "select_1"],
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check database connectivity for the active backend profile.")
    parser.parse_args()
    print(json.dumps(asyncio.run(run_check()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
