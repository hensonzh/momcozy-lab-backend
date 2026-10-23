"""Independent stateless stream: no DB/storage and strict structured results."""
import asyncio
import base64
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.infrastructure.stool_specialty_auth import mint_specialty_service_token
from services.stool_specialty.app import create_app
from services.stool_specialty.contracts import PhotoObservation, StoolAnalysisResult
from services.stool_specialty.settings import SpecialtySettings

SECRET = "test-only-stool-analyze-secret-" + "x" * 40


def payload():
    analysis, baby, photo = uuid4(), uuid4(), uuid4()
    return {
        "schema_version": "stool.analyze.v1",
        "analysis_id": str(analysis), "market": "US", "language": "zh",
        "baby_context": {
            "baby_context_snapshot_id": str(baby), "profile_version": 1,
            "birth_date": None, "feeding_mode": "unknown",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
        "photos": [{
            "photo_id": str(photo), "position": 0, "original_sha256": "a" * 64,
            "jpeg_b64": base64.b64encode(b"\xff\xd8\xffsynthetic").decode(),
        }],
    }


def token(body, *, market="US", secret=SECRET):
    return mint_specialty_service_token(
        secret=secret, analysis_id=body["analysis_id"], market=market,
        purpose="stool.analyze",
    )


def settings(*, enabled=False):
    return SpecialtySettings(
        app_env="test", market="US", service_shared_secret=SECRET,
        analysis_enabled=enabled, provider="openai" if enabled else "disabled",
        openai_api_key="test-key" if enabled else "",
    )


class Analyzer:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def analyze(self, value):
        self.calls.append(value)
        if self.fail:
            raise RuntimeError("private provider failure")
        return StoolAnalysisResult(
            assessment_status="assessed", color="yellow", consistency="mushy",
            findings=["none"], risk_level="routine",
            summary="从照片看为常见黄色糊状外观。",
            care_advice="继续观察宝宝整体状态和后续排便变化。",
            photo_observations=[PhotoObservation(
                position=0, image_quality="adequate", color="yellow",
                consistency="mushy", findings=["none"], note="主体清楚。",
            )],
        )


def test_closed_by_default_and_success_stream_is_ordered_and_bounded():
    async def run():
        body = payload()
        disabled = create_app(settings())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disabled), base_url="http://test") as http:
            assert (await http.post("/v1/analyze/stream", json=body,
                headers={"Authorization": "Bearer " + token(body)})).status_code == 503
        analyzer = Analyzer()
        service = create_app(settings(enabled=True), analyzer=analyzer)
        assert not hasattr(service.state, "sessions")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service), base_url="http://test") as http:
            assert (await http.post("/v1/analyze/stream", json=body)).status_code == 401
            response = await http.post("/v1/analyze/stream", json=body,
                headers={"Authorization": "Bearer " + token(body)})
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "private, no-store"
            frames = [frame for frame in response.text.strip().split("\n\n") if frame]
            assert [line.split(": ", 1)[1] for frame in frames for line in [frame.splitlines()[1]]] == [
                "stool.analysis.started", "stool.field.ready", "stool.field.ready",
                "stool.field.ready", "stool.field.ready", "stool.field.ready",
                "stool.field.ready", "stool.analysis.completed",
            ]
            assert [int(frame.splitlines()[0][4:]) for frame in frames] == list(range(2, 10))
            assert '"field":"summary"' in response.text
            assert '"event_type":"stool.analysis.completed"' in response.text
            assert "jpeg_b64" not in response.text and "synthetic" not in response.text
            assert len(analyzer.calls) == 1
            assert (await http.get("/healthz")).json() == {"status": "ready"}
    asyncio.run(run())


def test_model_failure_is_safe_terminal_failure_without_leakage():
    async def run():
        body = payload()
        service = create_app(settings(enabled=True), analyzer=Analyzer(fail=True))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service), base_url="http://test") as http:
            response = await http.post("/v1/analyze/stream", json=body,
                headers={"Authorization": "Bearer " + token(body)})
            assert response.status_code == 200
            assert "stool.analysis.started" in response.text
            assert "stool.analysis.failed" in response.text
            assert "private provider failure" not in response.text
            assert "stool.field.ready" not in response.text
    asyncio.run(run())


def test_scope_and_payload_fail_closed():
    async def run():
        body = payload()
        service = create_app(settings(enabled=True), analyzer=Analyzer())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service), base_url="http://test") as http:
            for auth in (token(body, market="CN"), token(body, secret="forged-" + SECRET)):
                assert (await http.post("/v1/analyze/stream", json=body,
                    headers={"Authorization": "Bearer " + auth})).status_code in (401, 403)
            invalid = dict(body, photos=body["photos"] * 4)
            invalid_response = await http.post("/v1/analyze/stream", json=invalid,
                headers={"Authorization": "Bearer " + token(body)})
            assert invalid_response.status_code == 422
            assert invalid_response.json() == {"detail": {"code": "specialty_input_invalid"}}
            assert "synthetic" not in invalid_response.text and "jpeg_b64" not in invalid_response.text
            injected = dict(body, owner_user_id=str(uuid4()))
            assert (await http.post("/v1/analyze/stream", json=injected,
                headers={"Authorization": "Bearer " + token(body)})).status_code == 422
    asyncio.run(run())
