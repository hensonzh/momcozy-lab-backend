from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.dependencies import require_current_user
from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser, issue_access_token
from app.modules.voice.router import get_voice_service
from tests.auth_key_material import auth_settings


def test_transcribe_chunk_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).post(
        "/v1/speech/transcribe-chunk",
        files={"file": ("speech.wav", b"abc", "audio/wav")},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_transcribe_chunk_returns_disabled_error_when_provider_is_not_configured() -> None:
    app = create_app(Settings(app_env="test", voice_provider="disabled"))
    _override_current_user(app, uuid4())

    response = TestClient(app).post(
        "/v1/speech/transcribe-chunk",
        files={"file": ("speech.wav", b"abc", "audio/wav")},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_provider_disabled"


def test_transcribe_chunk_local_stub_uses_current_user_contract() -> None:
    app = create_app(Settings(app_env="test", voice_provider="local_stub"))
    _override_current_user(app, uuid4())

    response = TestClient(app).post(
        "/v1/speech/transcribe-chunk",
        data={"language": "zh-CN"},
        files={"file": ("speech.wav", b"abc", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"text": ""}


def test_realtime_voice_stream_keeps_token_out_of_url_and_returns_pcm_contract() -> None:
    app = create_app(Settings(app_env="test", voice_provider="local_stub"))
    _override_current_user(app, uuid4())

    response = TestClient(app).get(
        "/v1/realtime-voice-stream?text=hello",
        headers={"Authorization": "Bearer ignored-by-override"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/pcm")
    assert response.headers["x-mai-audio-format"] == "pcm16"
    assert response.headers["x-mai-audio-sample-rate"] == "24000"
    assert response.headers["x-mai-audio-channels"] == "1"
    assert response.content == b""


def test_realtime_voice_stream_returns_disabled_error_when_provider_is_not_configured() -> None:
    app = create_app(Settings(app_env="test", voice_provider="disabled"))
    _override_current_user(app, uuid4())

    response = TestClient(app).get(
        "/v1/realtime-voice-stream?text=hello",
        headers={"Authorization": "Bearer ignored-by-override"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_provider_disabled"


def test_realtime_voice_stream_prefetches_before_sending_pcm_headers() -> None:
    app = create_app(Settings(app_env="test", voice_provider="local_stub"))
    _override_current_user(app, uuid4())
    app.dependency_overrides[get_voice_service] = lambda: _FirstChunkFailVoiceService()

    response = TestClient(app).get(
        "/v1/realtime-voice-stream?text=hello",
        headers={"Authorization": "Bearer ignored-by-override"},
    )

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "doubao_realtime_voice_failed"


def test_voice_capability_is_authenticated_and_disabled_without_a_real_provider() -> None:
    app = create_app(Settings(app_env="test", voice_provider="disabled"))
    assert TestClient(app).get("/v1/realtime-voice-capability").status_code == 401
    _override_current_user(app, uuid4())
    response = TestClient(app).get("/v1/realtime-voice-capability")
    assert response.status_code == 200
    assert response.json() == {"available": False}
    app = create_app(Settings(app_env="test", voice_provider="doubao", voice_api_key="test-key"))
    _override_current_user(app, uuid4())
    assert TestClient(app).get("/v1/realtime-voice-capability").json() == {"available": True}


def test_private_voice_stream_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).post(
        "/v1/realtime-voice-stream",
        json={"text": "Singapore private assistant reply"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_private_voice_stream_uses_body_and_owner_without_putting_text_in_url() -> None:
    owner = uuid4()
    service = _CapturedVoiceService()
    app = create_app(Settings(app_env="test", voice_provider="local_stub"))
    _override_current_user(app, owner)
    app.dependency_overrides[get_voice_service] = lambda: service

    response = TestClient(app).post(
        "/v1/realtime-voice-stream",
        json={"text": "Private maternal reply"},
        headers={"Authorization": "Bearer ignored-by-override"},
    )

    assert response.status_code == 200
    assert str(response.request.url) == "http://testserver/v1/realtime-voice-stream"
    assert service.calls == [(owner, "Private maternal reply")]
    assert response.content == b"\x00\x00\x01\x00"
    assert response.headers["content-type"].startswith("audio/pcm")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-mai-audio-format"] == "pcm16"
    assert response.headers["x-mai-audio-sample-rate"] == "24000"
    assert response.headers["x-mai-audio-channels"] == "1"


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": " "}, {"text": "x" * 4001}])
def test_private_voice_stream_rejects_empty_or_oversized_text(body: dict[str, str]) -> None:
    service = _CapturedVoiceService()
    app = create_app(Settings(app_env="test", voice_provider="local_stub"))
    _override_current_user(app, uuid4())
    app.dependency_overrides[get_voice_service] = lambda: service

    response = TestClient(app).post(
        "/v1/realtime-voice-stream",
        json=body,
        headers={"Authorization": "Bearer ignored-by-override"},
    )

    assert response.status_code == 422
    assert service.calls == []


def test_private_voice_stream_never_reads_reply_text_from_query_string() -> None:
    app = create_app(Settings(app_env="test", voice_provider="local_stub"))
    _override_current_user(app, uuid4())
    service = _CapturedVoiceService()
    app.dependency_overrides[get_voice_service] = lambda: service
    response = TestClient(app).post(
        "/v1/realtime-voice-stream?text=private-reply",
        json={"text": "approved body"},
        headers={"Authorization": "Bearer ignored-by-override"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert service.calls == []


def test_private_voice_stream_keeps_disabled_and_prefetch_error_contracts() -> None:
    app = create_app(Settings(app_env="test", voice_provider="disabled"))
    _override_current_user(app, uuid4())
    response = TestClient(app).post(
        "/v1/realtime-voice-stream",
        json={"text": "hello"},
        headers={"Authorization": "Bearer ignored-by-override"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_provider_disabled"

    app.dependency_overrides[get_voice_service] = lambda: _FirstChunkFailVoiceService()
    failed = TestClient(app).post(
        "/v1/realtime-voice-stream",
        json={"text": "hello"},
        headers={"Authorization": "Bearer ignored-by-override"},
    )
    assert failed.status_code == 502
    assert failed.headers["content-type"].startswith("application/json")
    assert failed.json()["error"]["code"] == "doubao_realtime_voice_failed"


def test_private_voice_openapi_requires_body_not_text_query() -> None:
    operations = create_app(Settings(app_env="test")).openapi()["paths"]["/v1/realtime-voice-stream"]
    operation = operations["post"]
    assert operation["requestBody"]["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"]
    assert not any(parameter.get("name") == "text" and parameter.get("in") == "query"
                   for parameter in operation.get("parameters", []))
    for method in ("get", "post"):
        media = operations[method]["responses"]["200"]["content"]
        assert "audio/pcm" in media
        assert "application/json" not in media


def test_realtime_voice_session_sends_disabled_error_frame() -> None:
    user_id = uuid4()
    settings = auth_settings(voice_provider="disabled", auth_require_active_session=False)
    token, _expires_in = issue_access_token(user_id=user_id, session_id=uuid4(), settings=settings)
    client = TestClient(create_app(settings))

    with client.websocket_connect("/v1/realtime-voice-session", headers={"Authorization": f"Bearer {token}"}) as websocket:
        frame = websocket.receive_json()

    assert frame["type"] == "error"
    assert frame["code"] == "voice_provider_disabled"


def test_realtime_voice_session_requires_authorization_header() -> None:
    client = TestClient(create_app(Settings(app_env="test")))

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/realtime-voice-session"):
            pass

    assert exc.value.code == 1008


def _override_current_user(app, user_id: UUID) -> None:
    async def fake_current_user() -> CurrentUser:
        return CurrentUser(
            user_id=user_id,
            subject=str(user_id),
            session_id="session",
            token_id="token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        )

    app.dependency_overrides[require_current_user] = fake_current_user


class _FirstChunkFailVoiceService:
    def synthesize_pcm_stream(self, *, actor_user_id: UUID, text: str):
        async def stream():
            raise ApiError(
                code="doubao_realtime_voice_failed",
                message="Failed to stream Doubao realtime voice.",
                status=502,
            )
            if False:
                yield b""

        return stream()


class _CapturedVoiceService:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    def synthesize_pcm_stream(self, *, actor_user_id: UUID, text: str):
        self.calls.append((actor_user_id, text))

        async def stream():
            yield b"\x00\x00"
            yield b"\x01\x00"

        return stream()
