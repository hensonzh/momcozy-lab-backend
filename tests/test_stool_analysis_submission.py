"""Synthetic-only draft submission; it must never invoke medical processing."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import ApiError
from app.infrastructure.db.base import Base
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisEvent, StoolAnalysisPhoto
from app.modules.stool_analyses.submission import SyntheticStoolSubmission
from app.modules.stool_analyses.job_builder import StoolSpecialtyInputBuilder
from app.modules.stool_photos.models import StoolPhoto
from app.modules.stool_photos.rules import validate_original
from app.modules.users.models import User
from test_stool_photo_rules import image_bytes


class Storage:
    def __init__(self):
        self.data = {}
        self.reads = 0

    async def get_bytes(self, *, key):
        self.reads += 1
        return self.data[key]


class Idempotency:
    def __init__(self):
        self.entries = {}

    async def reserve(self, *, actor_user_id, scope, key, request_hash, expires_at):
        index = actor_user_id, scope, key
        if index not in self.entries:
            self.entries[index] = SimpleNamespace(request_hash=request_hash, response_ref="")
            return SimpleNamespace(status="reserved", record=self.entries[index])
        record = self.entries[index]
        if record.request_hash != request_hash:
            raise ApiError(code="idempotency_conflict", message="Conflicting request.", status=409)
        return SimpleNamespace(status="replay", record=record)

    async def mark_completed(self, *, record, response_ref):
        record.response_ref = response_ref
        return record


class Audit:
    def __init__(self):
        self.calls = []

    async def record(self, **kwargs):
        self.calls.append(kwargs)


async def fixture(sessions, storage):
    owner, baby, thread, photo_id = (uuid4() for _ in range(4))
    body = image_bytes()
    digest = sha256(body).hexdigest()
    metadata = validate_original(body)
    key = f"synthetic/stool/{photo_id}"
    storage.data[key] = body
    async with sessions.begin() as session:
        session.add(User(id=owner, status="active"))
        session.add(BabyProfile(id=baby, owner_user_id=owner, name="Synthetic infant", birth_date=(datetime.now(timezone.utc).date() - timedelta(days=90))))
        session.add(StoolPhoto(
            id=photo_id, owner_user_id=owner, object_key=key, sha256=digest,
            content_type=metadata.content_type, size_bytes=len(body),
            width=metadata.width, height=metadata.height,
            review_status="passed", reason_codes=[], review_attempt=1,
            review_sha256=digest, review_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
    return owner, baby, thread, photo_id, key


async def prepare(sessions, storage, fixture_ids, *, ids=None, owner=None, baby=None, key="one", idem=None, audit=None, at=None, market="CN", language="zh"):
    original_owner, original_baby, thread, photo_id, _ = fixture_ids
    async with sessions.begin() as session:
        return await SyntheticStoolSubmission(
            session=session, storage=storage, idempotency=idem or Idempotency(), audit=audit or Audit(),
        ).prepare(
            owner=owner or original_owner, baby_id=baby or original_baby,
            trusted_thread_id=thread, photo_ids=ids if ids is not None else [photo_id],
            idempotency_key=key, request_id="synthetic-test", now=at,
            trusted_market=market, language=language,
        )


def test_synthetic_submission_is_ordered_persistent_and_idempotent(tmp_path):
    async def run():
        url = f"sqlite+aiosqlite:///{tmp_path / 'submit.db'}"
        engine = create_async_engine(url)
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__, StoolAnalysisEvent.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage, idem, audit = Storage(), Idempotency(), Audit()
        ids = await fixture(sessions, storage)
        first = await prepare(sessions, storage, ids, idem=idem, audit=audit)
        second = await prepare(sessions, storage, ids, idem=idem, audit=audit)
        assert first.id == second.id and first.status == "queued"
        assert first.final_result is None and first.result_validated_at is None
        assert first.baby_context_snapshot_id is not None
        assert (first.market, first.language, first.baby_profile_version, first.baby_feeding_mode) == (
            "CN", "zh", 1, "unknown",
        )
        assert len(audit.calls) == 1 and audit.calls[0]["action"] == "stool_analysis.submit"
        await engine.dispose()
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 1
            links = (await session.scalars(select(StoolAnalysisPhoto))).all()
            assert [(link.position, link.photo_id) for link in links] == [(0, ids[3])]
            assert links[0].original_sha256 == sha256(storage.data[ids[4]]).hexdigest()
            events = (await session.scalars(select(StoolAnalysisEvent))).all()
            assert [(item.sequence, item.event_type) for item in events] == [(1, "stool.card.created")]
            assert (await session.get(StoolAnalysis, first.id)).run_attempt == 0
        await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    "empty", "duplicate", "foreign_owner", "foreign_baby", "not_passed", "no_expiry",
    "expired", "purged", "hash_changed", "storage_tampered", "storage_missing",
    "missing_birth_date", "too_old", "future_birth_date",
])
def test_invalid_submission_creates_no_task_or_event(tmp_path, change):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'invalid.db'}")
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__, StoolAnalysisEvent.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage, idem, audit = Storage(), Idempotency(), Audit()
        ids = await fixture(sessions, storage)
        options = {}
        if change == "empty":
            options["ids"] = []
        elif change == "duplicate":
            options["ids"] = [ids[3], ids[3]]
        elif change == "foreign_owner":
            options["owner"] = uuid4()
        elif change == "foreign_baby":
            options["baby"] = uuid4()
        elif change == "storage_tampered":
            storage.data[ids[4]] = b"tampered"
        elif change == "storage_missing":
            del storage.data[ids[4]]
        elif change in {"missing_birth_date", "too_old", "future_birth_date"}:
            async with sessions.begin() as session:
                profile = await session.get(BabyProfile, ids[1])
                profile.birth_date = {
                    "missing_birth_date": None,
                    "too_old": datetime.now(timezone.utc).date() - timedelta(days=367),
                    "future_birth_date": datetime.now(timezone.utc).date() + timedelta(days=1),
                }[change]
        else:
            async with sessions.begin() as session:
                photo = await session.get(StoolPhoto, ids[3])
                if change == "not_passed":
                    photo.review_status = "inconclusive"
                elif change == "no_expiry":
                    photo.review_expires_at = None
                elif change == "expired":
                    photo.review_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                elif change == "purged":
                    photo.purged_at = datetime.now(timezone.utc)
                elif change == "hash_changed":
                    photo.sha256 = "b" * 64
        with pytest.raises(ApiError):
            await prepare(sessions, storage, ids, idem=idem, audit=audit, **options)
        async with sessions.begin() as session:
            assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 0
            assert await session.scalar(select(func.count()).select_from(StoolAnalysisEvent)) == 0
        assert idem.entries == {} and audit.calls == []
        await engine.dispose()
    asyncio.run(run())


def test_same_key_cannot_be_reused_for_a_changed_ordered_selection(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'conflict.db'}")
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__, StoolAnalysisEvent.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage, idem, audit = Storage(), Idempotency(), Audit()
        ids = await fixture(sessions, storage)
        first = await prepare(sessions, storage, ids, idem=idem, audit=audit)
        extra_id = uuid4()
        async with sessions.begin() as session:
            original = await session.get(StoolPhoto, ids[3])
            session.add(StoolPhoto(
                id=extra_id, owner_user_id=ids[0], object_key=f"synthetic/stool/{extra_id}",
                sha256=original.sha256, content_type=original.content_type,
                size_bytes=original.size_bytes, width=original.width, height=original.height,
                review_status="passed", reason_codes=[], review_attempt=1,
                review_sha256=original.sha256,
                review_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
        storage.data[f"synthetic/stool/{extra_id}"] = storage.data[ids[4]]
        with pytest.raises(ApiError) as captured:
            await prepare(sessions, storage, ids, ids=[extra_id, ids[3]], idem=idem, audit=audit)
        assert captured.value.code == "idempotency_conflict"
        assert len(audit.calls) == 1
        async with sessions.begin() as session:
            assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 1
            assert (await session.get(StoolAnalysis, first.id)).status == "queued"
        await engine.dispose()
    asyncio.run(run())


def test_two_approved_photos_keep_explicit_order(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ordered.db'}")
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__, StoolAnalysisEvent.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = Storage()
        ids = await fixture(sessions, storage)
        second_id = uuid4()
        second_key = f"synthetic/stool/{second_id}"
        storage.data[second_key] = storage.data[ids[4]]
        async with sessions.begin() as session:
            original = await session.get(StoolPhoto, ids[3])
            session.add(StoolPhoto(
                id=second_id, owner_user_id=ids[0], object_key=second_key,
                sha256=original.sha256, content_type=original.content_type,
                size_bytes=original.size_bytes, width=original.width, height=original.height,
                review_status="passed", reason_codes=[], review_attempt=1,
                review_sha256=original.sha256,
                review_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
        analysis = await prepare(sessions, storage, ids, ids=[second_id, ids[3]])
        async with sessions.begin() as session:
            links = (await session.scalars(select(StoolAnalysisPhoto).where(
                StoolAnalysisPhoto.analysis_id == analysis.id,
            ).order_by(StoolAnalysisPhoto.position))).all()
            assert [link.photo_id for link in links] == [second_id, ids[3]]
            assert [link.position for link in links] == [0, 1]
        await engine.dispose()
    asyncio.run(run())


def test_synthetic_prepare_public_contract_is_closed_without_thread_binding() -> None:
    from app.core.settings import Settings
    from app.factory import create_app

    schema = create_app(Settings(app_env="test")).openapi()
    assert "post" in schema["paths"]["/v1/stool-analyses"]
    assert Settings().stool_analysis_enabled is False
    assert "/v1/stool-analyses/submit" not in schema["paths"]


def test_synthetic_submission_enforces_180_day_review_boundary(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-window.db'}")
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__, StoolAnalysisEvent.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = Storage()
        ids = await fixture(sessions, storage)
        reviewed_at = datetime.now(timezone.utc) - timedelta(days=180)
        expiry = reviewed_at + timedelta(days=180)
        async with sessions.begin() as session:
            photo = await session.get(StoolPhoto, ids[3])
            photo.reviewed_at = reviewed_at
            photo.review_expires_at = expiry
        analysis = await prepare(sessions, storage, ids, key="before", at=expiry - timedelta(microseconds=1))
        assert analysis.status == "queued"
        with pytest.raises(ApiError) as error:
            await prepare(sessions, storage, ids, key="at-boundary", at=expiry)
        assert error.value.code == "stool_photos_not_approved"
        async with sessions.begin() as session:
            assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 1
        await engine.dispose()
    asyncio.run(run())


def test_pinned_baby_context_and_photo_refs_survive_profile_edit(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pinned.db'}")
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__, StoolAnalysisEvent.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = Storage()
        ids = await fixture(sessions, storage)
        async with sessions.begin() as session:
            baby = await session.get(BabyProfile, ids[1])
            baby.birth_date = datetime(2026, 5, 1, tzinfo=timezone.utc).date()
            baby.feeding_mode = "mixed_feeding"
            baby.version = 3
        analysis = await prepare(sessions, storage, ids, market="US", language="en")
        async with sessions.begin() as session:
            baby = await session.get(BabyProfile, ids[1])
            baby.birth_date = None
            baby.feeding_mode = "unknown"
            baby.version = 4
        async with sessions.begin() as session:
            (await session.get(StoolAnalysis, analysis.id)).status = "running"
        async with sessions.begin() as session:
            job = await StoolSpecialtyInputBuilder(session=session, storage=storage).build(analysis.id)
            assert (job.analysis_id, job.market, job.language) == (analysis.id, "US", "en")
            assert job.baby_context.baby_context_snapshot_id == analysis.baby_context_snapshot_id
            assert "2026-05-01" in job.model_dump_json()
            assert "mixed_feeding" in job.model_dump_json()
            assert "Synthetic infant" not in job.model_dump_json()
            assert [(ref.photo_id, ref.position, ref.original_sha256) for ref in job.photos] == [
                (ids[3], 0, sha256(storage.data[ids[4]]).hexdigest()),
            ]
            pinned = await session.get(StoolAnalysis, analysis.id)
            assert (str(pinned.baby_birth_date), pinned.baby_feeding_mode, pinned.baby_profile_version) == (
                "2026-05-01", "mixed_feeding", 3,
            )
            assert pinned.final_result is None
        storage.data[ids[4]] = b"mutated after admission"
        async with sessions.begin() as session:
            with pytest.raises(ApiError):
                await StoolSpecialtyInputBuilder(session=session, storage=storage).build(analysis.id)
        await engine.dispose()
    asyncio.run(run())


def test_idempotency_rejects_changed_market_or_language(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'locale.db'}")
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__, StoolAnalysisEvent.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage, idem = Storage(), Idempotency()
        ids = await fixture(sessions, storage)
        first = await prepare(sessions, storage, ids, key="same", idem=idem, market="CN", language="zh")
        for market, language in (("US", "zh"), ("CN", "en")):
            with pytest.raises(ApiError) as error:
                await prepare(sessions, storage, ids, key="same", idem=idem, market=market, language=language)
            assert error.value.code == "idempotency_conflict"
        async with sessions.begin() as session:
            assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 1
            assert (await session.get(StoolAnalysis, first.id)).language == "zh"
        await engine.dispose()
    asyncio.run(run())


def test_age_scope_uses_the_first_birthday_not_a_fixed_day_count(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'birthday.db'}")
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__, StoolAnalysisEvent.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = Storage()
        ids = await fixture(sessions, storage)
        birthday = datetime(2026, 10, 23, tzinfo=timezone.utc)
        async with sessions.begin() as session:
            (await session.get(BabyProfile, ids[1])).birth_date = birthday.date().replace(year=2025)
        accepted = await prepare(sessions, storage, ids, key="birthday", at=birthday)
        assert accepted.status == "queued"
        with pytest.raises(ApiError) as error:
            await prepare(sessions, storage, ids, key="after-birthday", at=birthday + timedelta(days=1))
        assert error.value.code == "stool_analysis_age_unsupported"
        await engine.dispose()

    asyncio.run(run())
