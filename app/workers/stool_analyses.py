"""Product-owned stool analysis queue worker."""
from __future__ import annotations

import asyncio
import logging
from typing import Literal, cast

import httpx

from ..core.settings import Settings
from ..infrastructure.db import create_db_engine, create_session_factory
from ..infrastructure.object_storage import create_object_storage
from ..modules.stool_analyses.dispatch_pump import StoolAnalysisDispatchPump
from ..modules.stool_analyses.transport_client import SpecialtyClient

LOGGER = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings.from_env()
    settings.validate_for_startup()
    if not settings.stool_analysis_enabled:
        LOGGER.info("Stool analysis worker is disabled.")
        await asyncio.Event().wait()
        return
    engine = create_db_engine(settings)
    sessions = create_session_factory(engine)
    storage = create_object_storage(settings)
    timeout = httpx.Timeout(settings.stool_specialty_request_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            client = SpecialtyClient(
                http, origin=settings.stool_specialty_origin,
                market=cast(Literal["CN", "US"], settings.stool_specialty_market),
                secret=settings.stool_specialty_service_secret,
                allow_insecure_test=settings.app_env in {"local", "test"},
            )
            pump = StoolAnalysisDispatchPump(
                sessions=sessions, storage=storage, client=client,
                market=cast(Literal["CN", "US"], settings.stool_specialty_market),
            )
            while True:
                try:
                    if await pump.run_once():
                        continue
                except Exception:
                    # Never put provider/image/result details into ordinary worker logs.
                    LOGGER.warning("Stool analysis worker iteration failed.")
                await asyncio.sleep(2)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
