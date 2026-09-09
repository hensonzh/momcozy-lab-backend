from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from livekit import api
from livekit.api.twirp_client import TwirpError

from ...core.settings import Settings


@dataclass(frozen=True)
class VideoCredentials:
    server_url: str | None
    token: str | None = field(repr=False)
    expires_at: datetime


class VideoProvider(Protocol):
    name: str
    async def create(self, room_name: str) -> None: ...
    async def close(self, room_name: str) -> None: ...
    async def participants(self, room_name: str) -> set[str]: ...
    def credentials(self, *, room_name: str, participant_identity: str) -> VideoCredentials: ...


class DisabledVideoProvider:
    name = "disabled"
    async def create(self, room_name: str) -> None:
        raise RuntimeError("Video service is disabled")
    async def close(self, room_name: str) -> None:
        raise RuntimeError("Video service is disabled")
    async def participants(self, room_name: str) -> set[str]:
        return set()
    def credentials(self, *, room_name: str, participant_identity: str) -> VideoCredentials:
        raise RuntimeError("Video service is disabled")


class SandboxVideoProvider:
    """No remote video: two authenticated clients exercise the persisted room workflow."""
    name = "sandbox"
    async def create(self, room_name: str) -> None:
        return None
    async def close(self, room_name: str) -> None:
        return None
    async def participants(self, room_name: str) -> set[str]:
        return set()
    def credentials(self, *, room_name: str, participant_identity: str) -> VideoCredentials:
        return VideoCredentials(server_url=None, token=None, expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))


class LiveKitVideoProvider:
    name = "livekit"
    def __init__(self, *, url: str, api_key: str, api_secret: str) -> None:
        self.url, self._api_key, self._api_secret = url, api_key, api_secret

    def _client(self) -> api.LiveKitAPI:
        return api.LiveKitAPI(self.url, self._api_key, self._api_secret)

    async def create(self, room_name: str) -> None:
        async with asyncio.timeout(10), self._client() as client:
            await client.room.create_room(api.CreateRoomRequest(name=room_name, empty_timeout=300, departure_timeout=30, max_participants=2))

    async def close(self, room_name: str) -> None:
        try:
            async with asyncio.timeout(10), self._client() as client:
                await client.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except TwirpError as error:
            if error.code != "not_found" and error.status != 404:
                raise

    async def participants(self, room_name: str) -> set[str]:
        try:
            async with asyncio.timeout(10), self._client() as client:
                response = await client.room.list_participants(api.ListParticipantsRequest(room=room_name))
                return {value.identity for value in response.participants}
        except TwirpError as error:
            if error.code == "not_found" or error.status == 404:
                return set()
            raise

    def credentials(self, *, room_name: str, participant_identity: str) -> VideoCredentials:
        ttl = timedelta(seconds=60)
        token = (api.AccessToken(self._api_key, self._api_secret).with_identity(participant_identity).with_ttl(ttl)
            .with_grants(api.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True, can_publish_data=False,
                can_publish_sources=["camera", "microphone"]))
            .to_jwt())
        return VideoCredentials(server_url=self.url, token=token, expires_at=datetime.now(timezone.utc) + ttl)


def build_video_provider(settings: Settings) -> VideoProvider:
    if settings.consultation_video_provider == "disabled":
        return DisabledVideoProvider()
    if settings.consultation_video_provider == "sandbox":
        if settings.is_production:
            raise ValueError("Production cannot use sandbox video")
        return SandboxVideoProvider()
    if settings.consultation_video_provider == "livekit":
        if not settings.consultation_livekit_url or not settings.consultation_livekit_api_key or not settings.consultation_livekit_api_secret:
            raise ValueError("LiveKit credentials are required")
        settings.validate_for_startup()
        return LiveKitVideoProvider(url=settings.consultation_livekit_url, api_key=settings.consultation_livekit_api_key, api_secret=settings.consultation_livekit_api_secret)
    raise ValueError("Unknown video provider")
