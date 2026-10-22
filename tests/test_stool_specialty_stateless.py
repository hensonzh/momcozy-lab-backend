"""Independent stateless synthetic stream: no DB, storage or medical conclusion."""
import asyncio
import base64
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.infrastructure.stool_specialty_auth import mint_specialty_service_token
from services.stool_specialty.app import create_app
from services.stool_specialty.settings import SpecialtySettings

SECRET = "test-only-stool-analyze-secret-" + "x" * 40


def payload():
    analysis, baby, photo = uuid4(), uuid4(), uuid4()
    return {
        "schema_version": "stool.analyze.synthetic.v1",
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


def test_default_closed_and_one_scoped_terminal_stream():
    async def run():
        body = payload()
        disabled = create_app(SpecialtySettings(app_env="test", market="US", service_shared_secret=SECRET))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disabled), base_url="http://test") as http:
            assert (await http.post("/v1/analyze/stream", json=body,
                headers={"Authorization": "Bearer " + token(body)})).status_code == 503
        service = create_app(SpecialtySettings(
            app_env="test", market="US", service_shared_secret=SECRET, synthetic_analyze_enabled=True,
        ))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service), base_url="http://test") as http:
            assert (await http.post("/v1/analyze/stream", json=body)).status_code == 401
            response = await http.post("/v1/analyze/stream", json=body,
                headers={"Authorization": "Bearer " + token(body)})
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "private, no-store"
            assert "id: 2\nevent: stool.analysis.started\n" in response.text
            assert "id: 3\nevent: stool.analysis.failed\n" in response.text
            assert response.text.index("stool.analysis.started") < response.text.index("stool.analysis.failed")
            assert "stool.analysis.completed" not in response.text
            assert "jpeg_b64" not in response.text and "synthetic" not in response.text
            assert (await http.get("/healthz")).status_code == 200
    asyncio.run(run())


def test_scope_and_payload_fail_closed():
    async def run():
        body = payload()
        service = create_app(SpecialtySettings(
            app_env="test", market="US", service_shared_secret=SECRET, synthetic_analyze_enabled=True,
        ))
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


def test_production_cannot_enable_synthetic_intake():
    import pytest
    with pytest.raises(ValueError, match="only in tests"):
        create_app(SpecialtySettings(app_env="production", market="US",
                                    service_shared_secret=SECRET, synthetic_analyze_enabled=True))
