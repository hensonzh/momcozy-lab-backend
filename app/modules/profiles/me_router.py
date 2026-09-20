"""Authenticated Me UI contracts. Shared baby/pump records use existing APIs."""

from typing import Any
from datetime import datetime
from uuid import UUID
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ..auth import CurrentUser
from ..records.router import get_records_service
from ..baby.models import BabyRecord
from ..audit import AuditService
from ..audit.repository import AuditRepository
from .me_models import MePreferences, MotherObservation
from .me_schemas import Concern, MeProfilePatch, Observation, RecordOrder
from .repository import ProfileRepository
from .router import get_profile_service, get_lactation_context_service

router = SurfaceAPIRouter(
    prefix="/profile/me-experience",
    tags=["profile"],
    api_surface_metadata=api_surface("public_app_api", owner="profiles", clients=["flutter"]),
)


async def _preferences(session: AsyncSession, user_id: UUID, *, create: bool = False) -> MePreferences | None:
    if create:
        await ProfileRepository(session).lock_profile_owner(owner_user_id=user_id)
    result = await session.get(MePreferences, user_id)
    if result is None and create:
        result = MePreferences(owner_user_id=user_id, profile={}, concerns=[], record_order=[])
        session.add(result)
        await session.flush()
    return result


async def _profile(session: AsyncSession, user_id: UUID, preferences: MePreferences | None) -> dict[str, Any]:
    repository = ProfileRepository(session)
    user = await repository.get_user_profile(user_id=user_id)
    mother = await repository.get_maternal_profile(owner_user_id=user_id)
    values = dict(preferences.profile if preferences else {})
    values.update(
        preferred_name=user.preferred_name if user else None,
        age=user.age if user else None,
        actual_delivery_date=mother.latest_delivery_date if mother else None,
        delivery_count=mother.delivery_count if mother else None,
        current_delivery_method=mother.latest_delivery_method if mother else None,
    )
    return values


async def _audit(session: AsyncSession, user_id: UUID, request: Request, action: str) -> None:
    await AuditService(repository=AuditRepository(session)).record(
        actor_user_id=user_id,
        action=action,
        resource_type="me_experience",
        resource_id=str(user_id),
        request_id=str(getattr(request.state, "request_id", "")),
    )


@router.get("")
async def read_me(
    start: datetime, end: datetime, current_user: CurrentUser = Depends(require_current_user), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    if start.tzinfo is None or end.tzinfo is None or end <= start or (end - start).total_seconds() > 172800:
        raise ApiError(code="validation_failed", message="Invalid day window.", status=422)
    owner = current_user.user_id
    preferences = await _preferences(session, owner)
    records = (
        await session.scalars(
            select(MotherObservation)
            .where(MotherObservation.owner_user_id == owner, MotherObservation.occurred_at >= start, MotherObservation.occurred_at < end)
            .order_by(MotherObservation.occurred_at)
        )
    ).all()
    return {
        "profile": await _profile(session, owner, preferences),
        "concerns": preferences.concerns if preferences else [],
        "order": preferences.record_order if preferences else [],
        "records": [Observation.model_validate(e).model_dump(mode="json") for e in records],
    }


@router.patch("/profile")
async def update_profile(
    payload: MeProfilePatch,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    owner = current_user.user_id
    preferences = await _preferences(session, owner, create=True)
    assert preferences is not None
    values = payload.model_dump(exclude_unset=True)
    user = {k: v for k, v in values.items() if k in {"preferred_name", "age"}}
    mother = {k: v for k, v in values.items() if k in {"actual_delivery_date", "delivery_count", "current_delivery_method"}}
    if user:
        await get_profile_service(session).update_user_profile(user_id=owner, values=user)
    if mother:
        await get_lactation_context_service(session).update_maternal_profile(owner_user_id=owner, values=mother)
    extra = {k: v for k, v in payload.model_dump(mode="json", exclude_unset=True).items() if k not in user and k not in mother}
    preferences.profile = {**preferences.profile, **extra}
    await session.flush()
    await _audit(session, owner, request, "profiles.me.update")
    return await _profile(session, owner, preferences)


@router.put("/concerns/{concern_id}", response_model=Concern)
async def put_concern(
    concern_id: UUID,
    payload: Concern,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> Concern:
    if concern_id != payload.id:
        raise ApiError(code="validation_failed", message="Concern ID mismatch.", status=422)
    preferences = await _preferences(session, current_user.user_id, create=True)
    assert preferences is not None
    previous = [e for e in preferences.concerns if e["id"] != str(concern_id)]
    if len(previous) >= 100:
        raise ApiError(code="validation_failed", message="Concern limit reached.", status=422)
    ended = next((e for e in preferences.concerns if e["id"] == str(concern_id) and e["ended"]), None)
    if ended is not None and not payload.ended:
        raise ApiError(code="validation_failed", message="Ended concerns cannot be resumed.", status=422)
    preferences.concerns = [*previous, payload.model_dump(mode="json")]
    await session.flush()
    await _audit(session, current_user.user_id, request, "profiles.concern.save")
    return payload


@router.put("/order", response_model=RecordOrder)
async def put_order(
    payload: RecordOrder,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> RecordOrder:
    preferences = await _preferences(session, current_user.user_id, create=True)
    assert preferences is not None
    preferences.record_order = list(payload.order)
    await session.flush()
    await _audit(session, current_user.user_id, request, "profiles.record_order.save")
    return payload


@router.put("/records/{record_id}", response_model=Observation)
async def put_record(
    record_id: UUID,
    payload: Observation,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> Observation:
    if record_id != payload.id:
        raise ApiError(code="validation_failed", message="Record ID mismatch.", status=422)
    await ProfileRepository(session).lock_profile_owner(owner_user_id=current_user.user_id)
    if payload.fields.feeding_record_id is not None:
        feeding = await session.scalar(select(BabyRecord).where(
            BabyRecord.id == payload.fields.feeding_record_id,
            BabyRecord.owner_user_id == current_user.user_id,
            BabyRecord.kind == "feeding",
            BabyRecord.deleted_at.is_(None),
        ))
        if feeding is None or payload.kind not in {"pain", "latch", "bottle"}:
            raise ApiError(code="validation_failed", message="Linked feeding record is unavailable.", status=422)
    record = await session.get(MotherObservation, (current_user.user_id, record_id))
    if payload.kind == "pump":
        if record is not None:
            # A retried PUT may not create a second canonical pumping event.
            existing = Observation.model_validate(record)
            if (
                existing.value != payload.value
                or existing.occurred_at != payload.occurred_at
                or existing.fields.model_dump(exclude={"canonical_record_id"}) != payload.fields.model_dump(exclude={"canonical_record_id"})
            ):
                raise ApiError(code="conflict", message="This pumping submission was already saved.", status=409)
            return existing
        pumping = await get_records_service(session).create_pumping(
            owner_user_id=current_user.user_id,
            pump_start_time=payload.occurred_at,
            milk_volume_ml=payload.fields.volume_ml,
            duration_seconds=payload.fields.duration_minutes * 60 if payload.fields.duration_minutes else None,
            pump_type="manual",
            source="manual",
            idempotency_key=str(payload.id),
            request_id=str(getattr(request.state, "request_id", "")),
        )
        payload.fields.canonical_record_id = str(pumping.id)
    if record is None:
        record = MotherObservation(owner_user_id=current_user.user_id, id=record_id)
        session.add(record)
    record.kind = payload.kind
    record.occurred_at = payload.occurred_at
    record.value = payload.value
    record.fields = payload.fields.model_dump(mode="json", exclude_none=True)
    await session.flush()
    await _audit(session, current_user.user_id, request, "profiles.observation.save")
    return payload
