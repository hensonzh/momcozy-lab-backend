from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..core.settings import Settings
from ..infrastructure.db.session import create_db_engine, create_session_factory
from ..infrastructure.video.provider import VideoProvider, build_video_provider
from ..modules.appointments.service import utc_now
from ..modules.consultations.room_models import CareConsultation, CareVideoCommand

LOGGER = logging.getLogger("production_backend.care_video")


async def process_next(sessions: async_sessionmaker[AsyncSession], video: VideoProvider, *, now: Callable[[], datetime] = utc_now) -> bool:
    """A committed command survives process loss; both provider operations are idempotent."""
    async with sessions.begin() as session:
        command = await session.scalar(select(CareVideoCommand).where(CareVideoCommand.status == "pending", CareVideoCommand.available_at <= now())
            .order_by(CareVideoCommand.available_at, CareVideoCommand.id).limit(1).with_for_update(skip_locked=True))
        if command is None:
            return False
        room = await session.scalar(select(CareConsultation).where(CareConsultation.id == command.consultation_id).with_for_update())
        assert room is not None
        current = room.room_name == command.room_name
        obsolete = command.operation == "create" and (not current or room.room_status not in {"creating", "failed"} or room.status not in {"waiting_room", "in_progress"})
        if not obsolete:
            command.attempts += 1
            try:
                if video.name != room.video_provider:
                    raise RuntimeError("Video provider configuration does not match this room")
                if command.operation == "create":
                    await video.create(command.room_name)
                else:
                    await video.close(command.room_name)
            except Exception as error:
                command.last_error = type(error).__name__[:80]
                command.available_at = now() + timedelta(seconds=min(300, 2 ** min(command.attempts, 8)))
                if current and command.operation == "create":
                    room.room_status = "failed"
                LOGGER.warning("Video command %s will retry (%s)", command.id, command.last_error)
                return True
            if current:
                room.room_status = "ready" if command.operation == "create" else "closed"
        command.status, command.completed_at, command.last_error = "done", now(), None
        return True


async def run() -> None:
    settings = Settings.from_env()
    settings.validate_for_startup()
    video = build_video_provider(settings)
    if video.name == "disabled":
        raise RuntimeError("Configure CONSULTATION_VIDEO_PROVIDER before starting the video worker")
    engine = create_db_engine(settings)
    sessions = create_session_factory(engine)
    try:
        while True:
            if not await process_next(sessions, video):
                await asyncio.sleep(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
