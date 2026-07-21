import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.agent_runtime.context.image_assets import AgentImageAccessService
from app.agent_runtime.providers.openai_responses import _responses_input_items
from app.modules.files.models import FileObject


def test_image_access_reuses_one_signed_url_for_the_same_thread_and_asset() -> None:
    now = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    owner_user_id = uuid4()
    thread_id = uuid4()
    image = _image(owner_user_id=owner_user_id)
    repository = FakeAgentRuntimeRepository(thread_id=thread_id, owner_user_id=owner_user_id)
    storage = FakeObjectStorage()
    service = AgentImageAccessService(
        repository=repository,
        file_repository=FakeFileRepository(image),
        object_storage=storage,
        url_ttl=timedelta(days=7),
        clock=lambda: now,
    )

    first = asyncio.run(
        service.ensure_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            asset_id=image.id,
        )
    )
    second = asyncio.run(
        service.ensure_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            asset_id=image.id,
        )
    )

    assert first == second
    assert storage.sign_calls == [
        {
            "key": image.object_key,
            "expires_in_seconds": 7 * 24 * 60 * 60,
        }
    ]
    assert repository.access_records[(thread_id, image.id)].expires_at == now + timedelta(days=7)


def test_image_access_persists_a_separate_binding_for_each_agent_thread() -> None:
    owner_user_id = uuid4()
    first_thread_id = uuid4()
    second_thread_id = uuid4()
    image = _image(owner_user_id=owner_user_id)
    repository = FakeAgentRuntimeRepository(thread_id=first_thread_id, owner_user_id=owner_user_id)
    repository.threads[second_thread_id] = owner_user_id
    storage = FakeObjectStorage()
    service = AgentImageAccessService(
        repository=repository,
        file_repository=FakeFileRepository(image),
        object_storage=storage,
        url_ttl=timedelta(days=7),
    )

    asyncio.run(
        service.ensure_for_thread(
            thread_id=first_thread_id,
            owner_user_id=owner_user_id,
            asset_id=image.id,
        )
    )
    asyncio.run(
        service.ensure_for_thread(
            thread_id=second_thread_id,
            owner_user_id=owner_user_id,
            asset_id=image.id,
        )
    )

    assert len(storage.sign_calls) == 2
    assert set(repository.access_records) == {
        (first_thread_id, image.id),
        (second_thread_id, image.id),
    }


def test_image_access_rotates_once_after_expiry_then_reuses_the_new_url() -> None:
    current_time = [datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)]
    owner_user_id = uuid4()
    thread_id = uuid4()
    image = _image(owner_user_id=owner_user_id)
    repository = FakeAgentRuntimeRepository(thread_id=thread_id, owner_user_id=owner_user_id)
    storage = FakeObjectStorage()
    service = AgentImageAccessService(
        repository=repository,
        file_repository=FakeFileRepository(image),
        object_storage=storage,
        url_ttl=timedelta(days=7),
        clock=lambda: current_time[0],
    )

    first = asyncio.run(
        service.ensure_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            asset_id=image.id,
        )
    )
    current_time[0] += timedelta(days=7, seconds=1)
    renewed = asyncio.run(
        service.ensure_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            asset_id=image.id,
        )
    )
    reused = asyncio.run(
        service.ensure_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            asset_id=image.id,
        )
    )

    assert renewed != first
    assert reused == renewed
    assert len(storage.sign_calls) == 2


def test_image_access_rejects_cross_owner_assets_and_non_https_urls() -> None:
    owner_user_id = uuid4()
    thread_id = uuid4()
    foreign_image = _image(owner_user_id=uuid4())
    service = AgentImageAccessService(
        repository=FakeAgentRuntimeRepository(thread_id=thread_id, owner_user_id=owner_user_id),
        file_repository=FakeFileRepository(foreign_image),
        object_storage=FakeObjectStorage(),
        url_ttl=timedelta(days=7),
    )

    with pytest.raises(ApiError) as cross_owner_error:
        asyncio.run(
            service.ensure_for_thread(
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                asset_id=foreign_image.id,
            )
        )

    assert cross_owner_error.value.code == "invalid_agent_attachment"

    owned_image = _image(owner_user_id=owner_user_id)
    insecure_service = AgentImageAccessService(
        repository=FakeAgentRuntimeRepository(thread_id=thread_id, owner_user_id=owner_user_id),
        file_repository=FakeFileRepository(owned_image),
        object_storage=FakeObjectStorage(url_scheme="http"),
        url_ttl=timedelta(days=7),
    )

    with pytest.raises(ApiError) as insecure_url_error:
        asyncio.run(
            insecure_service.ensure_for_thread(
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                asset_id=owned_image.id,
            )
        )

    assert insecure_url_error.value.code == "agent_image_url_unavailable"


def test_responses_input_materialization_replaces_asset_id_without_mutating_context() -> None:
    thread_id = uuid4()
    owner_user_id = uuid4()
    asset_id = uuid4()
    context = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "请看图片"},
                {"type": "input_image", "asset_id": str(asset_id), "detail": "high"},
            ],
        }
    ]
    calls = []

    async def resolve(**kwargs) -> str:
        calls.append(kwargs)
        return "https://images.example.test/thread-stable?signature=one"

    materialized = asyncio.run(
        _responses_input_items(
            context,
            thread_id=str(thread_id),
            actor_user_id=str(owner_user_id),
            image_url_resolver=resolve,
        )
    )

    assert materialized == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "请看图片"},
                {
                    "type": "input_image",
                    "image_url": "https://images.example.test/thread-stable?signature=one",
                    "detail": "high",
                },
            ],
        }
    ]
    assert context[0]["content"][1] == {
        "type": "input_image",
        "asset_id": str(asset_id),
        "detail": "high",
    }
    assert calls == [
        {
            "thread_id": str(thread_id),
            "actor_user_id": str(owner_user_id),
            "asset_id": str(asset_id),
        }
    ]


class FakeAgentRuntimeRepository:
    def __init__(self, *, thread_id: UUID, owner_user_id: UUID) -> None:
        self.threads = {thread_id: owner_user_id}
        self.access_records = {}

    async def get_thread_for_owner(self, *, thread_id: UUID, owner_user_id: UUID):
        if self.threads.get(thread_id) != owner_user_id:
            return None
        return SimpleNamespace(id=thread_id, owner_user_id=owner_user_id)

    async def get_image_access(self, *, thread_id: UUID, asset_id: UUID):
        return self.access_records.get((thread_id, asset_id))

    async def upsert_image_access(self, *, thread_id: UUID, asset_id: UUID, image_url: str, expires_at: datetime):
        record = SimpleNamespace(
            thread_id=thread_id,
            asset_id=asset_id,
            image_url=image_url,
            expires_at=expires_at,
        )
        self.access_records[(thread_id, asset_id)] = record
        return record


class FakeFileRepository:
    def __init__(self, file_object: FileObject) -> None:
        self.file_object = file_object

    async def get_for_owner(self, *, file_id: UUID, owner_user_id: UUID):
        if self.file_object.id != file_id or self.file_object.owner_user_id != owner_user_id:
            return None
        return self.file_object


class FakeObjectStorage:
    def __init__(self, *, url_scheme: str = "https") -> None:
        self.url_scheme = url_scheme
        self.sign_calls = []

    async def create_presigned_get_url(self, **kwargs) -> str:
        self.sign_calls.append(kwargs)
        return f"{self.url_scheme}://images.example.test/{len(self.sign_calls)}?signature=stable"


def _image(*, owner_user_id: UUID) -> FileObject:
    image_id = uuid4()
    return FileObject(
        id=image_id,
        owner_user_id=owner_user_id,
        object_key=f"users/{owner_user_id}/files/{image_id}/photo.png",
        original_filename="photo.png",
        content_type="image/png",
        size_bytes=7,
        status="active",
    )
