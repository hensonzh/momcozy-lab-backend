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


def test_realtime_voice_session_sends_disabled_error_frame() -> None:
    user_id = uuid4()
    settings = Settings(
        app_env="test",
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
        auth_jwt_issuer="momcozy-test",
        auth_jwt_audience="momcozy-app",
        voice_provider="disabled",
    )
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
