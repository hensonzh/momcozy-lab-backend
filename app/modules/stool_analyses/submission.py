"""Synthetic, unwired admission slice; no clinical analysis is dispatched.

Only a future principal-bound route may call this with a verified thread ID.
The caller owns the database transaction so task, links, card, idem and audit
commit together. The photo/review/retention policy is not released yet.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal, Sequence
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ...infrastructure.object_storage import ObjectStorage
from ..audit import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from ..baby.profile_models import BabyProfile
from ..stool_photos.models import StoolPhoto
from ..stool_photos.review import require_approved_photos
from ..stool_photos.rules import MAX_ORIGINAL_BYTES, validate_original
from ..users.models import User
from .events import StoolNonmedicalEventLedger
from .models import StoolAnalysis, StoolAnalysisPhoto
from .transport_contract import SpecialtyTransportEvent


def _not_approved() -> ApiError:
    return ApiError(code="stool_photos_not_approved", message="Every photo must have a current approved review.", status=409)


class StoolAnalysisSubmission:
    """Prepare a queued record/card only; no public API, network job or medical result."""

    def __init__(self, *, session: AsyncSession, storage: ObjectStorage,
                 idempotency: IdempotencyService, audit: AuditService) -> None:
        self.session = session
        self.storage = storage
        self.idempotency = idempotency
        self.audit = audit

    async def prepare(
        self, *, owner: UUID, baby_id: UUID, trusted_thread_id: UUID,
        photo_ids: Sequence[UUID], idempotency_key: str, request_id: str,
        trusted_market: Literal["CN", "US"], language: Literal["zh", "en"],
        now: datetime | None = None,
    ) -> StoolAnalysis:
        if trusted_market not in ("CN", "US") or language not in ("zh", "en"):
            raise ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=422)
        if not 1 <= len(photo_ids) <= 3 or len(set(photo_ids)) != len(photo_ids):
            raise ApiError(code="stool_photos_invalid_selection", message="Select 1 to 3 distinct photos.", status=422)
        at = now or datetime.now(timezone.utc)
        account = await self.session.scalar(select(User).where(User.id == owner).with_for_update())
        baby = await self.session.scalar(select(BabyProfile).where(
            BabyProfile.id == baby_id, BabyProfile.owner_user_id == owner,
        ).with_for_update())
        if (account is None or account.status != "active" or account.deleted_at is not None
                or baby is None or baby.deleted_at is not None):
            raise ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=409)
        if baby.birth_date is None:
            raise ApiError(code="stool_analysis_age_unsupported", message="Baby age is required for analysis.", status=409)
        age_days = (at.date() - baby.birth_date).days
        if age_days < 0 or age_days > 366:
            raise ApiError(code="stool_analysis_age_unsupported", message="Stool analysis supports babies from birth to one year.", status=409)
        rows = (await self.session.scalars(select(StoolPhoto).where(
            StoolPhoto.id.in_(photo_ids), StoolPhoto.owner_user_id == owner,
        ).order_by(StoolPhoto.id).with_for_update())).all()
        if len(rows) != len(photo_ids):
            raise _not_approved()
        by_id = {photo.id: photo for photo in rows}
        photos = [by_id[photo_id] for photo_id in photo_ids]
        require_approved_photos(photos, owner, now=at)
        # A database fingerprint alone cannot prove the private original still matches.
        for photo in photos:
            try:
                body = await self.storage.get_bytes(key=photo.object_key)
                if (len(body) > MAX_ORIGINAL_BYTES or len(body) != photo.size_bytes
                        or sha256(body).hexdigest() != photo.sha256):
                    raise _not_approved()
                metadata = validate_original(body)
                if (metadata.content_type != photo.content_type
                        or metadata.width != photo.width or metadata.height != photo.height):
                    raise _not_approved()
            except Exception as exc:
                raise _not_approved() from exc
        digest = request_hash({
            "baby_id": str(baby_id), "thread_id": str(trusted_thread_id),
            "market": trusted_market, "language": language,
            "photos": [(str(photo.id), photo.sha256) for photo in photos],
        })
        decision = await self.idempotency.reserve(
            actor_user_id=owner, scope="stool_analysis.submit", key=idempotency_key,
            request_hash=digest, expires_at=at + timedelta(days=1),
        )
        if decision.status == "replay":
            existing = await self.session.scalar(select(StoolAnalysis).where(
                StoolAnalysis.id == parse_idempotency_response_ref(decision.record.response_ref),
                StoolAnalysis.owner_user_id == owner, StoolAnalysis.baby_id == baby_id,
                StoolAnalysis.thread_id == trusted_thread_id,
                StoolAnalysis.deleted_at.is_(None), StoolAnalysis.revoked_at.is_(None),
            ))
            if existing is None:
                raise ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=409)
            return existing
        analysis = StoolAnalysis(
            id=uuid4(), owner_user_id=owner, baby_id=baby_id,
            thread_id=trusted_thread_id, status="queued", run_after=at,
            baby_context_snapshot_id=uuid4(), market=trusted_market, language=language,
            baby_profile_version=baby.version, baby_birth_date=baby.birth_date,
            baby_feeding_mode=baby.feeding_mode, context_captured_at=at,
        )
        self.session.add(analysis)
        await self.session.flush()
        self.session.add_all([
            StoolAnalysisPhoto(analysis_id=analysis.id, photo_id=photo.id,
                               position=position, original_sha256=photo.sha256)
            for position, photo in enumerate(photos)
        ])
        await StoolNonmedicalEventLedger(self.session).append(owner=owner, event=SpecialtyTransportEvent(
            schema_version="stool.events.v1", analysis_id=analysis.id,
            sequence=1, event_type="stool.card.created",
        ))
        await self.idempotency.mark_completed(record=decision.record, response_ref=str(analysis.id))
        await self.audit.record(
            actor_user_id=owner, action="stool_analysis.submit",
            resource_type="stool_analysis", resource_id=str(analysis.id), request_id=request_id,
            details={"photo_count": len(photos)},
        )
        return analysis


SyntheticStoolSubmission = StoolAnalysisSubmission
