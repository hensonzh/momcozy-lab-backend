"""A scoped Product request accepts only the approved nonmedical terminal stream."""
import asyncio
import base64
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import jwt
import pytest

from app.infrastructure.stool_specialty_auth import AUDIENCE, ISSUER
from app.modules.stool_analyses.transport_client import SpecialtyClient, SpecialtyPermanentError, SpecialtyTransientError
from app.modules.stool_analyses.transport_contract import SpecialtyAnalysisInput

SECRET = "test-only-stool-specialty-secret-" + "x" * 40


def payload():
    return SpecialtyAnalysisInput.model_validate({
        "analysis_id": uuid4(), "market": "US", "language": "en",
        "baby_context": {"baby_context_snapshot_id": uuid4(), "profile_version": 1,
                         "birth_date": None, "feeding_mode": "unknown", "captured_at": datetime.now(timezone.utc)},
        "photos": [{"photo_id": uuid4(), "position": 0, "original_sha256": "a" * 64,
                    "jpeg_b64": base64.b64encode(b"\xff\xd8\xffsynthetic").decode()}],
    })


def wire(analysis_id, *, event_type="stool.analysis.failed", sequence=2, extra=""):
    return (f'id: {sequence}\nevent: {event_type}\ndata: '
            f'{{"schema_version":"stool.events.v1","analysis_id":"{analysis_id}",'
            f'"sequence":{sequence},"event_type":"{event_type}"{extra}}}\n\n').encode()


def test_scoped_auth_and_single_stream_without_original():
    async def run():
        job = payload()
        def handler(request):
            claims = jwt.decode(request.headers["Authorization"].removeprefix("Bearer "), SECRET,
                                algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER)
            assert claims["purpose"] == "stool.analyze" and claims["analysis_id"] == str(job.analysis_id)
            assert claims["market"] == "US" and claims["exp"] - claims["iat"] == 60
            assert request.url.path == "/v1/analyze/stream"
            assert b"object_key" not in request.content and b"owner_user_id" not in request.content
            return httpx.Response(200, content=wire(job.analysis_id), headers={
                "Content-Type": "text/event-stream", "Cache-Control": "private, no-store",
            })
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET)
            events = [event async for event in client.analyze(job)]
            assert [(event.sequence, event.event_type) for event in events] == [(2, "stool.analysis.failed")]
    asyncio.run(run())


@pytest.mark.parametrize("body,status,error", [
    (lambda job: wire(uuid4()), 200, SpecialtyPermanentError),
    (lambda job: wire(job.analysis_id, event_type="stool.analysis.completed"), 200, SpecialtyPermanentError),
    (lambda job: wire(job.analysis_id, extra=',"risk_level":"invented"'), 200, SpecialtyPermanentError),
    (lambda job: wire(job.analysis_id, event_type="stool.analysis.started", sequence=2), 200, SpecialtyTransientError),
    (lambda job: wire(job.analysis_id, event_type="stool.analysis.started", sequence=2)
     + wire(job.analysis_id, event_type="stool.analysis.started", sequence=2), 200, SpecialtyPermanentError),
    (lambda job: wire(job.analysis_id, sequence=3), 200, SpecialtyPermanentError),
    (lambda job: wire(job.analysis_id, event_type="stool.analysis.started", sequence=2)
     + wire(job.analysis_id, sequence=4), 200, SpecialtyPermanentError),

    (lambda job: b"id: 2\nevent: stool.analysis.failed\ndata: {}", 200, SpecialtyTransientError),
    (lambda job: wire(job.analysis_id), 307, SpecialtyTransientError),
    (lambda job: wire(job.analysis_id), 503, SpecialtyTransientError),
    (lambda job: wire(job.analysis_id), 403, SpecialtyPermanentError),
])
def test_bad_stream_never_accepted(body, status, error):
    async def run():
        job = payload()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            status, content=body(job), headers={"Content-Type": "text/event-stream", "Cache-Control": "private, no-store"},
        ))) as http:
            client = SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET)
            with pytest.raises(error):
                [event async for event in client.analyze(job)]
    asyncio.run(run())


def test_client_accepts_ordered_progress_then_failure_only():
    async def run():
        job = payload()
        stream = (wire(job.analysis_id, event_type="stool.analysis.started", sequence=2)
                  + wire(job.analysis_id, event_type="stool.analysis.failed", sequence=3))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=stream, headers={"Content-Type": "text/event-stream", "Cache-Control": "private, no-store"},
        ))) as http:
            events = [entry async for entry in SpecialtyClient(
                http, origin="https://specialty.test", market="US", secret=SECRET,
            ).analyze(job)]
            assert [(entry.sequence, entry.event_type) for entry in events] == [
                (2, "stool.analysis.started"), (3, "stool.analysis.failed"),
            ]
    asyncio.run(run())


def test_client_rejects_insecure_origin_and_cross_region():
    async def run():
        job = payload()
        async with httpx.AsyncClient() as http:
            with pytest.raises(ValueError):
                SpecialtyClient(http, origin="http://public.example", market="US", secret=SECRET)
            client = SpecialtyClient(http, origin="https://specialty.test", market="CN", secret=SECRET)
            with pytest.raises(SpecialtyPermanentError):
                [event async for event in client.analyze(job)]
    asyncio.run(run())
