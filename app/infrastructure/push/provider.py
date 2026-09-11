from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import UUID

import httpx

from ...core.settings import Settings


@dataclass(frozen=True)
class PushMessage:
    notification_id: UUID
    binding_id: UUID
    expires_at: datetime
    badge_count: int | None = None


@dataclass(frozen=True)
class PushResult:
    outcome: Literal["sent", "retry", "invalid", "failed"]
    message_id: str | None = None
    error_code: str | None = None
    retry_after_seconds: int = 30


class PushProvider(Protocol):
    async def send(self, *, token: str, message: PushMessage) -> PushResult: ...


class FcmPushProvider:
    def __init__(self, *, project_id: str, access_token: Callable[[], Awaitable[str]], client: httpx.AsyncClient) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", project_id):
            raise ValueError("Invalid FCM project ID")
        self.project_id, self.access_token, self.client = project_id, access_token, client

    async def send(self, *, token: str, message: PushMessage) -> PushResult:
        ttl = max(0, int((message.expires_at - datetime.now(timezone.utc)).total_seconds()))
        if ttl == 0:
            return PushResult("failed", error_code="expired")
        payload = {"message": {
            "token": token,
            "notification": {"title": "Momcozy", "body": "You have a new update. Open the app to view it."},
            "data": {"notification_id": str(message.notification_id), "binding_id": str(message.binding_id)},
            "android": {"priority": "high", "ttl": f"{min(ttl, 2419200)}s", "collapse_key": "momcozy_updates",
                "notification": {"channel_id": "service_updates", "tag": str(message.notification_id), "visibility": "PRIVATE"}},
            "apns": {"headers": {"apns-expiration": str(int(message.expires_at.timestamp())),
                "apns-collapse-id": str(message.notification_id), "apns-push-type": "alert", "apns-priority": "10"},
                "payload": {"aps": {"sound": "default", **({"badge": max(0, message.badge_count)} if message.badge_count is not None else {})}}},
        }}
        try:
            credential = await self.access_token()
            response = await self.client.post(f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send",
                headers={"Authorization": f"Bearer {credential}"}, json=payload, timeout=10)
            data = response.json()
            if response.status_code == 200 and isinstance(data.get("name"), str):
                return PushResult("sent", message_id=data["name"][:255])
            details = data.get("error", {}).get("details", [])
            code = next((item.get("errorCode") for item in details if isinstance(item, dict)
                and item.get("@type") == "type.googleapis.com/google.firebase.fcm.v1.FcmError"), None)
            if code == "UNREGISTERED":
                return PushResult("invalid", error_code="token_unregistered")
            if response.status_code == 429 or response.status_code >= 500:
                raw_retry = response.headers.get("Retry-After", "30")
                retry = int(raw_retry) if raw_retry.isdigit() else 30
                return PushResult("retry", error_code="provider_unavailable", retry_after_seconds=min(3600, max(30, retry)))
            # A malformed message is not proof that a token is invalid.
            return PushResult("failed", error_code="provider_rejected")
        except (httpx.TransportError, ValueError, TypeError, AttributeError):
            return PushResult("retry", error_code="provider_unavailable")
        except Exception:
            # Credentials/SDK failures can contain key paths or provider content.
            return PushResult("retry", error_code="provider_auth_unavailable")


class GoogleAccessTokenSource:
    def __init__(self, path: str) -> None:
        from google.oauth2 import service_account
        self.credentials = service_account.Credentials.from_service_account_file(path,  # type: ignore[no-untyped-call]
            scopes=["https://www.googleapis.com/auth/firebase.messaging"])
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        async with self._lock:
            if not self.credentials.valid:
                await asyncio.wait_for(asyncio.to_thread(self._refresh), timeout=15)
            return str(self.credentials.token)

    def _refresh(self) -> None:
        from google.auth.transport.requests import Request
        transport = Request()
        def request(**kwargs: Any) -> Any:
            kwargs["timeout"] = 10
            return transport(**kwargs)
        self.credentials.refresh(request)


def build_push_provider(settings: Settings, client: httpx.AsyncClient) -> PushProvider | None:
    if settings.push_provider == "disabled":
        return None
    return FcmPushProvider(project_id=settings.push_fcm_project_id,
        access_token=GoogleAccessTokenSource(settings.push_fcm_credentials_file), client=client)
