"""Synthetic public contract keeps admission separate from authorized history."""
import json
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.dependencies import require_current_user
from app.core.settings import Settings
from app.factory import create_app
from app.modules.stool_analyses.public_router import (
    analysis_available, get_event_ledger, get_submission_service, verify_stool_thread,
    verify_stool_history_thread,
)


class FakeSubmission:
    def __init__(self, owner, baby, thread):
        self.owner, self.baby, self.thread = owner, baby, thread
        self.calls = []
        self.analysis_id = uuid4()

    async def prepare(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs['owner'] == self.owner
        assert kwargs['baby_id'] == self.baby
        assert kwargs['trusted_thread_id'] == self.thread
        return SimpleNamespace(id=self.analysis_id, status='queued')


class FakeLedger:
    def __init__(self, owner, analysis_id):
        self.owner, self.analysis_id = owner, analysis_id
        self.reads = []

    async def get_analysis(self, *, owner, analysis_id):
        assert owner == self.owner and analysis_id == self.analysis_id
        return SimpleNamespace(id=analysis_id, status='queued')

    async def list_for_thread(self, *, owner, thread_id, cursor=None, limit=50):
        assert owner == self.owner
        return [SimpleNamespace(id=self.analysis_id, baby_id=uuid4(), status='queued',
                                created_at=datetime.now(timezone.utc))], None

    async def list_after(self, *, owner, analysis_id, after, limit=50):
        assert owner == self.owner and analysis_id == self.analysis_id
        self.reads.append(after)
        return [] if after else [SimpleNamespace(
            analysis_id=analysis_id, sequence=1, schema_version='stool.events.v1',
            event_type='stool.card.created',
        )]


def test_explicit_analysis_deletion_still_requires_auth_when_analysis_closed():
    response = TestClient(create_app(Settings(app_env='test', stool_specialty_market='CN'))).delete(f'/v1/stool-analyses/{uuid4()}')
    assert response.status_code == 401


def test_unconfigured_analysis_rejects_new_submission_but_preserves_history():
    owner, baby, thread, photo = (uuid4() for _ in range(4))
    app = create_app(Settings(app_env='test', stool_specialty_market='CN'))
    service = FakeSubmission(owner, baby, thread)
    app.dependency_overrides[get_submission_service] = lambda: service
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    client = TestClient(app)
    payload = {'baby_id': str(baby), 'thread_id': str(thread), 'photo_ids': [str(photo)]}
    response = client.post('/v1/stool-analyses', json=payload, headers={'Idempotency-Key': 'once'})
    assert response.status_code == 503
    assert service.calls == []
    # Configuration blocks new submissions, not owner-scoped history access.
    ledger = FakeLedger(owner, service.analysis_id)
    app.dependency_overrides[get_event_ledger] = lambda: ledger
    app.dependency_overrides[verify_stool_history_thread] = lambda: thread
    assert client.get(f'/v1/stool-analyses/{service.analysis_id}').status_code == 200
    assert client.get(f'/v1/stool-analyses?thread_id={thread}').status_code == 200
    assert client.get(f'/v1/stool-analyses/{service.analysis_id}/events/stream').status_code == 200


def test_even_test_enabled_analysis_requires_configured_agent_thread_binding():
    owner, baby, thread, photo = (uuid4() for _ in range(4))
    app = create_app(Settings(app_env='test', stool_specialty_market='CN'))
    service = FakeSubmission(owner, baby, thread)
    app.dependency_overrides[get_submission_service] = lambda: service
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    app.dependency_overrides[analysis_available] = lambda: True
    response = TestClient(app).post('/v1/stool-analyses', json={
        'baby_id': str(baby), 'thread_id': str(thread), 'photo_ids': [str(photo)],
    }, headers={'Idempotency-Key': 'once', 'Authorization': 'Bearer scoped-token'})
    assert response.status_code == 503 and service.calls == []


def test_synthetic_submit_and_owner_scoped_card_event_replay():
    owner, baby, thread, photo = (uuid4() for _ in range(4))
    app = create_app(Settings(app_env='test', stool_specialty_market='CN'))
    service = FakeSubmission(owner, baby, thread)
    ledger = FakeLedger(owner, service.analysis_id)
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    app.dependency_overrides[analysis_available] = lambda: True
    app.dependency_overrides[verify_stool_thread] = lambda: thread
    app.dependency_overrides[verify_stool_history_thread] = lambda: thread
    app.dependency_overrides[get_submission_service] = lambda: service
    app.dependency_overrides[get_event_ledger] = lambda: ledger
    client = TestClient(app)
    unauthorized = TestClient(create_app(Settings(app_env='test', stool_specialty_market='CN'))).post('/v1/stool-analyses',
        json={'baby_id': str(baby), 'thread_id': str(thread), 'photo_ids': [str(photo)]},
        headers={'Idempotency-Key': 'once'})
    assert unauthorized.status_code == 401
    response = client.post('/v1/stool-analyses', json={
        'baby_id': str(baby), 'thread_id': str(thread), 'photo_ids': [str(photo)],
    }, headers={'Idempotency-Key': 'once'})
    assert response.status_code == 202
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.json() == {'analysis_id': str(service.analysis_id), 'status': 'queued'}
    assert service.calls[0]['photo_ids'] == [photo]
    assert (service.calls[0]['trusted_market'], service.calls[0]['language']) == ('CN', 'en')
    assert client.get(f'/v1/stool-analyses/{service.analysis_id}').json()['status'] == 'queued'
    history = client.get(f'/v1/stool-analyses?thread_id={thread}')
    assert history.status_code == 200 and history.json()['items'][0]['analysis_id'] == str(service.analysis_id)
    assert history.json()['next_cursor'] is None
    stream = client.get(f'/v1/stool-analyses/{service.analysis_id}/events/stream?after_sequence=0')
    assert stream.status_code == 200 and stream.headers['content-type'].startswith('text/event-stream')
    assert 'id: 1\nevent: stool.card.created\n' in stream.text
    data = next(line[6:] for line in stream.text.splitlines() if line.startswith('data: '))
    assert json.loads(data) == {
        'analysis_id': str(service.analysis_id), 'sequence': 1,
        'schema_version': 'stool.events.v1', 'event_type': 'stool.card.created',
    }
    assert client.get(f'/v1/stool-analyses/{service.analysis_id}/events/stream?after_sequence=1').text == ''
    assert ledger.reads == [0, 1]
    assert 'object_key' not in stream.text and 'image' not in stream.text


def test_test_gate_rejects_thread_mismatch_before_submission():
    owner, baby, thread, photo = (uuid4() for _ in range(4))
    app = create_app(Settings(app_env='test', stool_specialty_market='CN'))
    service = FakeSubmission(owner, baby, thread)
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    app.dependency_overrides[analysis_available] = lambda: True
    app.dependency_overrides[verify_stool_thread] = lambda: uuid4()
    app.dependency_overrides[get_submission_service] = lambda: service
    response = TestClient(app).post('/v1/stool-analyses', json={
        'baby_id': str(baby), 'thread_id': str(thread), 'photo_ids': [str(photo)],
    }, headers={'Idempotency-Key': 'once'})
    assert response.status_code == 409 and service.calls == []


def test_bound_agent_thread_is_verified_with_bearer_and_owner_before_synthetic_prepare():
    import asyncio

    import httpx
    from fastapi import Request

    from app.infrastructure.agent_runtime import RuntimeConversationGateway
    from app.modules.stool_analyses.public_router import get_stool_conversation_gateway

    owner, other, baby, thread, photo = (uuid4() for _ in range(5))
    app = create_app(Settings(app_env='test', stool_specialty_market='CN'))
    service = FakeSubmission(owner, baby, thread)
    app.dependency_overrides[get_submission_service] = lambda: service
    app.dependency_overrides[analysis_available] = lambda: True

    async def principal(request: Request):
        return SimpleNamespace(user_id=other if request.headers.get('X-Test-Owner') == 'other' else owner)

    app.dependency_overrides[require_current_user] = principal
    requests = []

    def agent_reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get('Authorization') != 'Bearer scoped-token':
            return httpx.Response(401)
        return httpx.Response(200, json={'id': str(thread), 'owner_user_id': str(owner)})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(agent_reply)) as upstream:
            app.dependency_overrides[get_stool_conversation_gateway] = lambda: RuntimeConversationGateway(
                upstream, 'https://agent.example',
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as http:
                payload = {'baby_id': str(baby), 'thread_id': str(thread), 'photo_ids': [str(photo)]}
                headers = {'Authorization': 'Bearer scoped-token', 'Idempotency-Key': 'once'}
                accepted = await http.post('/v1/stool-analyses', json=payload, headers=headers)
                assert accepted.status_code == 202, accepted.text
                assert len(service.calls) == 1
                assert requests[0].url == f'https://agent.example/v1/agent/threads/{thread}'
                denied = await http.post('/v1/stool-analyses', json=payload,
                                         headers={**headers, 'X-Test-Owner': 'other'})
                assert denied.status_code == 404 and len(service.calls) == 1
                no_bearer = await http.post('/v1/stool-analyses', json=payload,
                                            headers={'Idempotency-Key': 'once'})
                assert no_bearer.status_code == 401 and len(service.calls) == 1
                assert len(requests) == 2  # Missing bearer is never forwarded.
                app.dependency_overrides[analysis_available] = lambda: False
                closed = await http.post('/v1/stool-analyses', json=payload, headers=headers)
                assert closed.status_code == 503 and closed.json()['error']['code'] == 'stool_analysis_unavailable'
                assert len(requests) == 2 and len(service.calls) == 1

    asyncio.run(run())


def test_enabled_admission_rejects_missing_region_and_unapproved_language():
    owner, baby, thread, photo = (uuid4() for _ in range(4))
    service = FakeSubmission(owner, baby, thread)
    payload = {'baby_id': str(baby), 'thread_id': str(thread), 'photo_ids': [str(photo)]}
    app = create_app(Settings(app_env='test'))
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    app.dependency_overrides[analysis_available] = lambda: True
    app.dependency_overrides[verify_stool_thread] = lambda: thread
    app.dependency_overrides[get_submission_service] = lambda: service
    response = TestClient(app).post('/v1/stool-analyses', json=payload, headers={'Idempotency-Key': 'once'})
    assert response.status_code == 503 and service.calls == []

    scoped = create_app(Settings(app_env='test', stool_specialty_market='US'))
    scoped.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    scoped.dependency_overrides[analysis_available] = lambda: True
    scoped.dependency_overrides[verify_stool_thread] = lambda: thread
    scoped.dependency_overrides[get_submission_service] = lambda: service
    client = TestClient(scoped)
    bad = client.post('/v1/stool-analyses', json=payload, headers={
        'Idempotency-Key': 'once', 'Accept-Language': 'fr', 'X-Market': 'CN',
    })
    assert bad.status_code == 422 and service.calls == []
    accepted = client.post('/v1/stool-analyses', json=payload, headers={
        'Idempotency-Key': 'once', 'Accept-Language': 'zh', 'X-Market': 'CN',
    })
    assert accepted.status_code == 202
    assert (service.calls[0]['trusted_market'], service.calls[0]['language']) == ('US', 'zh')
