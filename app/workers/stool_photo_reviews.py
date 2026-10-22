"""Durable stool-photo review queue; run separately with python -m app.workers.stool_photo_reviews."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from hashlib import sha256

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..core.settings import Settings
from ..infrastructure.db.session import create_db_engine, create_session_factory
from ..infrastructure.object_storage import ObjectStorage, create_object_storage
from ..modules.stool_photos.models import StoolPhoto
from ..modules.stool_photos.review import (
    DecisionsReviewProvider, DisabledReviewProvider, ReviewOutcome, ReviewProvider,
    post_decisions, review_photo,
)
from ..modules.users.models import User

LOGGER = logging.getLogger(__name__)

LEASE = timedelta(minutes=2)
REVIEW_TTL = timedelta(days=180)  # Product-selected submission window; not clinical validation.


def review_provider(settings: Settings) -> ReviewProvider:
    if settings.stool_photo_review_provider != "decisions":
        return DisabledReviewProvider()
    if not settings.stool_photo_third_party_approved or not settings.openai_api_key:
        return DisabledReviewProvider()

    async def post(payload: dict[str, Any]) -> dict[str, Any]:
        return await post_decisions(payload, api_key=settings.openai_api_key)

    return DecisionsReviewProvider(post=post)


async def process_next(sessions: async_sessionmaker[AsyncSession], storage: ObjectStorage,
                       provider: ReviewProvider, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    async with sessions.begin() as session:
        photo = await session.scalar(
            select(StoolPhoto).join(User, User.id == StoolPhoto.owner_user_id).where(
                StoolPhoto.deleted_at.is_(None), User.deleted_at.is_(None), User.status == "active",
                or_(StoolPhoto.review_status == "pending", (
                    (StoolPhoto.review_status == "reviewing")
                    & (StoolPhoto.review_started_at < now - LEASE)
                )),
            ).order_by(StoolPhoto.created_at, StoolPhoto.id).with_for_update(of=StoolPhoto, skip_locked=True).limit(1)
        )
        if photo is None:
            return False
        photo.review_status = "reviewing"
        photo.review_attempt += 1
        photo.review_started_at = now
        photo_id, attempt, object_key, expected_hash = photo.id, photo.review_attempt, photo.object_key, photo.sha256

    if isinstance(provider, DisabledReviewProvider):
        # The cleanup worker can run before image-understanding approval without
        # even opening a pending child's photo. It may only mark it unavailable.
        result = ReviewOutcome("failed", ("review_unavailable",), "disabled-v1")
    else:
        try:
            body = await storage.get_bytes(key=object_key)
            if len(body) > 10 * 1024 * 1024 or sha256(body).hexdigest() != expected_hash:
                raise ValueError("Photo contents changed after upload")
            result = await review_photo(body, provider)
        except Exception:
            result = ReviewOutcome("failed", ("review_unavailable",), "failed-v1")

    async with sessions.begin() as session:
        # Compare lease attempt to prevent a timed-out worker overwriting a newer review.
        photo = await session.scalar(select(StoolPhoto).where(StoolPhoto.id == photo_id).with_for_update())
        if photo is None or photo.deleted_at is not None or photo.review_attempt != attempt or photo.review_status != "reviewing":
            return True
        photo.review_status = result.status
        photo.reason_codes = list(result.reason_codes)
        photo.policy_version = result.policy_version
        photo.review_sha256 = expected_hash if result.status == "passed" else None
        photo.reviewed_at = datetime.now(timezone.utc)
        photo.review_expires_at = photo.reviewed_at + REVIEW_TTL if result.status == "passed" else None
    return True


async def purge_deleted(sessions: async_sessionmaker[AsyncSession], storage: ObjectStorage) -> bool:
    now = datetime.now(timezone.utc)
    async with sessions.begin() as session:
        photo = await session.scalar(
            select(StoolPhoto).where(
                StoolPhoto.purged_at.is_(None),
                StoolPhoto.deleted_at.is_not(None),
            ).order_by(StoolPhoto.created_at, StoolPhoto.id).with_for_update(skip_locked=True).limit(1)
        )
        if photo is None:
            return False
        # If object deletion fails, roll back; next worker pass retries the tombstone.
        await storage.delete(key=photo.object_key)
        photo.purged_at = now
    return True


async def run() -> None:
    settings = Settings.from_env()
    settings.validate_for_startup()
    engine = create_db_engine(settings)
    sessions = create_session_factory(engine)
    storage = create_object_storage(settings)
    provider = review_provider(settings)
    try:
        await run_loop(sessions, storage, provider)
    finally:
        await engine.dispose()


async def run_loop(sessions: async_sessionmaker[AsyncSession], storage: ObjectStorage, provider: ReviewProvider) -> None:
    while True:
        try:
            if await purge_deleted(sessions, storage):
                continue
        except Exception:
            # A failed deletion must not starve unrelated pending reviews.
            LOGGER.warning("Stool photo purge failed; retrying later.")
        try:
            if await process_next(sessions, storage, provider):
                continue
        except Exception:
            # Provider/storage errors may contain private object keys. Log no exception details.
            LOGGER.warning("Stool photo review iteration failed; retrying later.")
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(run())
