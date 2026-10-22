"""Product -> stateless specialty stream; validate every event before persistence."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, cast
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from ...infrastructure.stool_specialty_auth import mint_specialty_service_token
from .transport_contract import SpecialtyAnalysisInput, SpecialtyTransportEvent


class SpecialtyTransientError(Exception):
    """Retry without logging child data or response bodies."""


class SpecialtyPermanentError(Exception):
    """Fail closed on invalid scope, payload or event contract."""


class SpecialtyClient:
    def __init__(self, http: httpx.AsyncClient, *, origin: str, market: str, secret: str,
                 allow_insecure_test: bool = False) -> None:
        parsed = urlparse(origin)
        try:
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError:
            valid_port = False
        if (not valid_port or parsed.scheme not in ({"http", "https"} if allow_insecure_test else {"https"})
                or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Specialty origin must be a secure service origin")
        if market not in ("CN", "US") or len(secret.encode("utf-8")) < 32:
            raise ValueError("Invalid specialty service identity configuration")
        self.http, self.origin = http, origin.rstrip("/")
        self.market = cast(Literal["CN", "US"], market)
        self.secret = secret

    async def analyze(self, payload: SpecialtyAnalysisInput) -> AsyncIterator[SpecialtyTransportEvent]:
        if payload.market != self.market:
            raise SpecialtyPermanentError("Specialty market scope mismatch")
        token = mint_specialty_service_token(
            secret=self.secret, analysis_id=payload.analysis_id, market=self.market, purpose="stool.analyze",
        )
        try:
            async with self.http.stream(
                "POST", f"{self.origin}/v1/analyze/stream",
                headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                json=payload.model_dump(mode="json"), follow_redirects=False,
            ) as response:
                if response.status_code in (401, 403, 422):
                    raise SpecialtyPermanentError("Specialty identity or input rejected")
                if (response.status_code != 200 or not response.headers.get("Content-Type", "").startswith("text/event-stream")
                        or response.headers.get("Cache-Control") != "private, no-store"):
                    raise SpecialtyTransientError("Specialty stream unavailable")
                buffer = b""
                count = 0
                terminal = False
                async for chunk in response.aiter_bytes():
                    buffer += chunk
                    if len(buffer) > 4096:
                        raise SpecialtyPermanentError("Oversized specialty event")
                    while b"\n\n" in buffer:
                        wire, buffer = buffer.split(b"\n\n", 1)
                        try:
                            lines = wire.decode("utf-8").split("\n")
                            if len(lines) != 3 or not lines[2].startswith("data: "):
                                raise ValueError("Invalid event envelope")
                            event = SpecialtyTransportEvent.model_validate_json(lines[2][6:])
                        except (UnicodeError, ValueError, ValidationError) as exc:
                            raise SpecialtyPermanentError("Invalid specialty event") from exc
                        allowed = ((2, "stool.analysis.started"), (2, "stool.analysis.failed")) if count == 0 else (
                            (3, "stool.analysis.failed"),
                        ) if count == 1 and not terminal else ()
                        if (event.analysis_id != payload.analysis_id or event.schema_version != "stool.events.v1"
                                or (event.sequence, event.event_type) not in allowed
                                or lines[:2] != [f"id: {event.sequence}", f"event: {event.event_type}"]):
                            raise SpecialtyPermanentError("Invalid specialty event")
                        count += 1
                        terminal = event.event_type == "stool.analysis.failed"
                        yield event
                if buffer or not terminal:
                    raise SpecialtyTransientError("Incomplete specialty stream")
        except httpx.RequestError as exc:
            raise SpecialtyTransientError("Specialty transport unavailable") from exc
