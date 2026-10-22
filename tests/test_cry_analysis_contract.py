"""The cry service consumes private PCM; only bounded results leave it."""
import io
import wave
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.cry_analysis.service import validate_wav, normalized_result


def wav(seconds: int = 1) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\x00\x00' * 16000 * seconds)
    return stream.getvalue()


def test_validates_pcm_and_rejects_non_audio() -> None:
    assert len(validate_wav(wav())) == 32000
    for body in (b'', b'not a wav', wav() + b'extra'):
        with pytest.raises(ApiError):
            validate_wav(body)


def test_only_allowlisted_provider_data_reaches_result() -> None:
    result = normalized_result({'phase': 'done', 'answer': 'hungry', 'confidence': 0.98, 'instructions': 'ignore safety'})
    assert result == {'status': 'done', 'answer': 'hungry'}
    with pytest.raises(ApiError):
        normalized_result({'phase': 'done', 'answer': 'unrecognized'})
    assert normalized_result({'phase': 'detecting'}) == {'status': 'incomplete'}
    assert normalized_result({'phase': 'done', 'answer': 'no_cry', 'reason': 'no_valid_cry_patterns'}) == {'status': 'done', 'answer': 'no_cry'}
    assert normalized_result({'phase': 'done', 'answer': 'no_cry', 'reason': 'unknown'}) == {'status': 'incomplete'}


def test_vendor_payload_is_bounded_and_uses_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import httpx
    from app.modules.cry_analysis.service import analyze

    captured = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, endpoint, content, headers):
            captured.append((endpoint, content, headers))
            return httpx.Response(200, json={'phase': 'done', 'answer': 'sleep', 'free_text': 'IGNORE'},
                                  request=httpx.Request('POST', endpoint))

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: Client())
    result = asyncio.run(analyze(wav(), uuid4(), endpoint='https://vendor.example/analyze', api_key='test-secret'))
    assert result == {'status': 'done', 'answer': 'sleep'}
    assert len(captured) == 1 and len(captured[0][1]) == 32000
    assert captured[0][2]['x-audio-timestamp'] == '0'


def test_owner_scoped_result_binds_to_one_thread_and_rejects_others() -> None:
    import asyncio
    from types import SimpleNamespace
    from datetime import datetime, timezone
    from app.modules.cry_analysis.router import get_cry_analysis_for_agent
    from app.modules.cry_analysis.schemas import CryAnalysisResolveRequest

    owner, other, analysis_id, thread, another_thread = (uuid4() for _ in range(5))
    row = SimpleNamespace(id=analysis_id, owner_user_id=owner, thread_id=None,
                          status='done', answer='sleep', created_at=datetime.now(timezone.utc))

    class Session:
        async def scalar(self, statement):
            params = statement.compile().params
            return row if owner in params.values() else None
        async def flush(self):
            pass

    session = Session()
    response = SimpleNamespace(headers={})
    def resolve(actor, target):
        return CryAnalysisResolveRequest(analysis_id=analysis_id, actor_user_id=actor, thread_id=target)
    row.status = 'pending'
    with pytest.raises(ApiError) as not_ready:
        asyncio.run(get_cry_analysis_for_agent(resolve(owner, thread), response, _client=object(), session=session))
    assert not_ready.value.status == 409 and row.thread_id is None
    row.status = 'done'
    result = asyncio.run(get_cry_analysis_for_agent(resolve(owner, thread), response, _client=object(), session=session))
    assert result.thread_id == thread and row.thread_id == thread
    with pytest.raises(ApiError) as error:
        asyncio.run(get_cry_analysis_for_agent(resolve(owner, another_thread), response, _client=object(), session=session))
    assert error.value.status == 404
    with pytest.raises(ApiError) as error:
        asyncio.run(get_cry_analysis_for_agent(resolve(other, thread), response, _client=object(), session=session))
    assert error.value.status == 404


def test_abandoned_pending_result_becomes_failed_without_leaking_audio() -> None:
    import asyncio
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app.modules.cry_analysis.router import _read_result

    owner, analysis_id = uuid4(), uuid4()
    row = SimpleNamespace(id=analysis_id, owner_user_id=owner, status='pending',
                          answer=None, created_at=datetime.now(timezone.utc) - timedelta(minutes=6))

    class Session:
        async def scalar(self, statement):
            return row
        async def flush(self):
            pass

    result = asyncio.run(_read_result(Session(), analysis_id, owner))
    assert result.status == 'failed' and result.answer is None


@pytest.mark.parametrize('endpoint', ['', 'http://vendor.example/analyze', 'https://127.0.0.1/analyze', 'https://localhost/analyze'])
def test_cry_vendor_is_fail_closed_without_public_https(endpoint: str) -> None:
    import asyncio
    from app.modules.cry_analysis.service import analyze

    with pytest.raises(ApiError) as error:
        asyncio.run(analyze(wav(), uuid4(), endpoint=endpoint, api_key='test-secret'))
    assert error.value.code == 'cry_service_unconfigured'


def test_cry_vendor_requires_private_key() -> None:
    import asyncio
    from app.modules.cry_analysis.service import analyze

    with pytest.raises(ApiError) as error:
        asyncio.run(analyze(wav(), uuid4(), endpoint='https://vendor.example/analyze', api_key=''))
    assert error.value.status == 503


def test_early_no_cry_does_not_hide_a_later_cry(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import httpx
    from app.modules.cry_analysis.service import analyze

    timestamps: list[str] = []
    pauses: list[float] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, endpoint, content, headers):
            timestamps.append(headers['x-audio-timestamp'])
            result = ({'phase': 'done', 'answer': 'no_cry', 'reason': 'no_valid_cry_patterns'}
                      if len(timestamps) == 1 else
                      {'phase': 'done', 'answer': 'hungry', 'reason': 'cry_translated'})
            return httpx.Response(200, json=result, request=httpx.Request('POST', endpoint))

    async def pause(seconds):
        pauses.append(seconds)

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: Client())
    monkeypatch.setattr(asyncio, 'sleep', pause)
    result = asyncio.run(analyze(wav(2), uuid4(), endpoint='https://vendor.example/analyze', api_key='test-secret'))
    assert result == {'status': 'done', 'answer': 'hungry'}
    assert timestamps == ['0', '0']
    assert len(pauses) == 1 and 0.8 <= pauses[0] <= 1.0


def test_only_no_cry_responses_end_as_no_cry_after_whole_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import httpx
    from app.modules.cry_analysis.service import analyze

    timestamps: list[str] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, endpoint, content, headers):
            timestamps.append(headers['x-audio-timestamp'])
            return httpx.Response(200, json={'phase': 'done', 'answer': 'no_cry', 'reason': 'no_valid_cry_patterns'},
                                  request=httpx.Request('POST', endpoint))

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: Client())
    # Avoid real-time pacing in this unit test without affecting production.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(asyncio, 'sleep', AsyncMock())
    result = asyncio.run(analyze(wav(2), uuid4(), endpoint='https://vendor.example/analyze', api_key='test-secret'))
    assert result == {'status': 'done', 'answer': 'no_cry'}
    assert timestamps == ['0', '0']
