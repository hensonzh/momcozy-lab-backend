from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from ...core.errors import ApiError
from ...infrastructure.object_storage import ObjectStorage
from ..audit import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .agent_url_cache import AgentFileUrlCache
from .models import FileObject
from .repository import FileRepository


FILE_UPLOAD_IDEMPOTENCY_SCOPE = "files.upload"
FILE_DELETE_IDEMPOTENCY_SCOPE = "files.delete"
IDEMPOTENCY_TTL = timedelta(hours=24)
DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class FileContent:
    file_object: FileObject
    body: bytes


class FileService:
    def __init__(
        self,
        *,
        repository: FileRepository,
        object_storage: ObjectStorage,
        audit_service: AuditService | None = None,
        idempotency_service: IdempotencyService | None = None,
        agent_file_url_cache: AgentFileUrlCache | None = None,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> None:
        self.repository = repository
        self.object_storage = object_storage
        self.audit_service = audit_service
        self.idempotency_service = idempotency_service
        self.agent_file_url_cache = agent_file_url_cache
        self.max_upload_bytes = max_upload_bytes

    async def upload(
        self,
        *,
        owner_user_id: UUID,
        filename: str,
        body: bytes,
        content_type: str,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> FileObject:
        if not body:
            raise ApiError(code="validation_failed", message="Uploaded file is empty.", status=422)
        if len(body) > self.max_upload_bytes:
            raise ApiError(
                code="payload_too_large",
                message="Uploaded file is too large.",
                status=413,
                details={"max_bytes": self.max_upload_bytes},
            )

        normalized_filename = _safe_filename(filename)
        normalized_content_type = content_type or "application/octet-stream"
        idempotency_record = None

        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=FILE_UPLOAD_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=_upload_request_hash(
                    filename=normalized_filename,
                    body=body,
                    content_type=normalized_content_type,
                ),
                expires_at=_utcnow() + IDEMPOTENCY_TTL,
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_upload(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        object_key = f"users/{owner_user_id}/files/{uuid4().hex}/{normalized_filename}"
        await self.object_storage.put_bytes(key=object_key, body=body, content_type=normalized_content_type)

        try:
            created = await self.repository.create(
                owner_user_id=owner_user_id,
                object_key=object_key,
                original_filename=normalized_filename,
                content_type=normalized_content_type,
                size_bytes=len(body),
            )
            if idempotency_record is not None and self.idempotency_service is not None:
                await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(created.id))
            if self.audit_service is not None:
                await self.audit_service.record(
                    actor_user_id=owner_user_id,
                    action="files.upload",
                    resource_type="file",
                    resource_id=str(created.id),
                    request_id=request_id,
                    details={"content_type": normalized_content_type, "size_bytes": len(body)},
                )
            return created
        except Exception:
            await self.object_storage.delete(key=object_key)
            raise

    async def get_for_owner(self, *, file_id: UUID, owner_user_id: UUID) -> FileObject:
        file_object = await self.repository.get_for_owner(file_id=file_id, owner_user_id=owner_user_id)
        if file_object is None:
            raise ApiError(code="not_found", message="File not found.", status=404)
        return file_object

    async def read_content_for_owner(self, *, file_id: UUID, owner_user_id: UUID) -> FileContent:
        file_object = await self.get_for_owner(file_id=file_id, owner_user_id=owner_user_id)
        body = await self.object_storage.get_bytes(key=file_object.object_key)
        return FileContent(file_object=file_object, body=body)

    async def list_for_owner(self, *, owner_user_id: UUID, limit: int = 50) -> list[FileObject]:
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_for_owner(owner_user_id=owner_user_id, limit=limit)

    async def delete_for_owner(
        self,
        *,
        file_id: UUID,
        owner_user_id: UUID,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> None:
        idempotency_record = None
        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=FILE_DELETE_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash({"file_id": str(file_id)}),
                expires_at=_utcnow() + IDEMPOTENCY_TTL,
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                if idempotency_record.response_ref:
                    return
                raise ApiError(code="idempotency_in_progress", message="Request is still in progress.", status=409)

        deleted = await self.repository.soft_delete_for_owner(
            file_id=file_id,
            owner_user_id=owner_user_id,
            deleted_at=_utcnow(),
        )
        if deleted is None:
            raise ApiError(code="not_found", message="File not found.", status=404)

        await self.object_storage.delete(key=deleted.object_key)
        if self.agent_file_url_cache is not None:
            await self.agent_file_url_cache.invalidate(
                owner_user_id=owner_user_id,
                file_id=deleted.id,
                object_key=deleted.object_key,
            )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="files.delete",
                resource_type="file",
                resource_id=str(deleted.id),
                request_id=request_id,
                details={"object_cleanup": "deleted"},
            )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(deleted.id))

    async def _replay_upload(self, *, owner_user_id: UUID, response_ref: str) -> FileObject:
        file_id = parse_idempotency_response_ref(response_ref)
        file_object = await self.repository.get_for_owner(file_id=file_id, owner_user_id=owner_user_id)
        if file_object is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return file_object


def _safe_filename(filename: str) -> str:
    name = PurePosixPath(str(filename or "").strip()).name
    return name or "upload.bin"


def _upload_request_hash(*, filename: str, body: bytes, content_type: str) -> str:
    return request_hash(
        {
            "filename": filename,
            "content_type": content_type,
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
