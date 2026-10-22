from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from ...core.errors import ApiError
from ...infrastructure.db.session import add_after_rollback_callback
from ...infrastructure.object_storage import ObjectStorage
from ..audit import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import StoolPhoto
from .repository import StoolPhotoRepository
from .rules import validate_original


class StoolPhotoService:
    def __init__(self, *, repository: StoolPhotoRepository, storage: ObjectStorage,
                 idempotency: IdempotencyService, audit: AuditService) -> None:
        self.repository = repository
        self.storage = storage
        self.idempotency = idempotency
        self.audit = audit

    async def upload(self, *, owner: UUID, body: bytes, key: str, request_id: str) -> StoolPhoto:
        metadata = validate_original(body)  # Must validate ORIGINAL bytes before compression/upload.
        if not await self.repository.lock_active_owner(owner):
            raise ApiError(code="account_unavailable", message="Account is not active.", status=403)
        digest = sha256(body).hexdigest()
        decision = await self.idempotency.reserve(
            actor_user_id=owner, scope="stool_photo.upload", key=key,
            request_hash=request_hash({"sha256": digest, "content_type": metadata.content_type}),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        if decision.status == "replay":
            existing = await self.repository.get_for_owner(parse_idempotency_response_ref(decision.record.response_ref), owner)
            if existing is None:
                raise ApiError(code="not_found", message="Photo not found.", status=404)
            return existing
        object_key = f"users/{owner}/stool-photos/{uuid4().hex}/original"
        # Register before writing: a partially successful object write followed by an
        # exception/cancellation must still be compensated when the request rolls back.
        add_after_rollback_callback(
            self.repository.session, lambda: self.storage.delete(key=object_key),
        )
        await self.storage.put_bytes(key=object_key, body=body, content_type=metadata.content_type)
        try:
            photo = StoolPhoto(
                owner_user_id=owner, object_key=object_key, sha256=digest,
                content_type=metadata.content_type, size_bytes=len(body),
                width=metadata.width, height=metadata.height,
                review_status="pending", reason_codes=[], review_attempt=0,
            )
            self.repository.session.add(photo)
            await self.repository.session.flush()
            await self.idempotency.mark_completed(record=decision.record, response_ref=str(photo.id))
            await self.audit.record(actor_user_id=owner, action="stool_photo.upload",
                                    resource_type="stool_photo", resource_id=str(photo.id), request_id=request_id,
                                    details={"content_type": metadata.content_type, "size_bytes": len(body)})
            return photo
        except Exception:
            await self.storage.delete(key=object_key)
            raise

    async def get(self, *, owner: UUID, photo_id: UUID) -> StoolPhoto:
        photo = await self.repository.get_for_owner(photo_id, owner)
        if photo is None:
            raise ApiError(code="not_found", message="Photo not found.", status=404)
        return photo

    async def delete(self, *, owner: UUID, photo_id: UUID, request_id: str) -> None:
        photo = await self.repository.get_including_deleted_for_owner(photo_id, owner)
        if photo is None:
            raise ApiError(code="not_found", message="Photo not found.", status=404)
        if photo.deleted_at is not None:
            return
        photo.deleted_at = datetime.now(timezone.utc)
        photo.review_status = "failed"
        photo.review_sha256 = None
        photo.reason_codes = ["photo_removed"]
        photo.review_expires_at = None
        await self.repository.revoke_linked_analyses(
            owner_user_id=owner, photo_id=photo_id, at=photo.deleted_at,
        )
        await self.audit.record(actor_user_id=owner, action="stool_photo.delete",
                                resource_type="stool_photo", resource_id=str(photo.id), request_id=request_id)
        # The durable purge worker removes bytes after the DB tombstone commits.
