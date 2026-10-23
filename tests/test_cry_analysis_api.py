"""Owner/idempotency/thread scope on the real database, with a fake classifier."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import require_agent_runtime_client, require_current_user
from app.infrastructure.db import get_session
from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser
from app.modules.cry_analysis import router as cry_router
from product_database import database, postgres
from test_cry_analysis_contract import wav


@postgres
@pytest.mark.parametrize('answer', ['sleep', 'no_cry'])
def test_idempotent_upload_is_private_and_agent_claims_one_thread(monkeypatch, answer: str) -> None:
    async def run() -> None:
        async with database() as (sessions, _, _, _, owners, _):
            calls: list[bytes] = []

            async def classify(body, owner, *, endpoint, api_key):
                assert owner == owners[0]
                calls.append(body)
                return {'status': 'done', 'answer': answer, 'reason_code': 'no_valid_cry_patterns' if answer == 'no_cry' else 'cry_translated'}

            monkeypatch.setattr(cry_router, 'analyze', classify)
            app = create_app(Settings(app_env='test', zoundream_endpoint='https://vendor.example/analyze', zoundream_api_key='synthetic'))
            active_owner = [owners[0]]

            async def auth():
                return CurrentUser(user_id=active_owner[0], subject=str(active_owner[0]),
                                   session_id='session', token_id='token', roles=frozenset({'user'}), permissions=frozenset())

            async def session():
                async with sessions() as db:
                    try:
                        yield db
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise

            async def service():
                return object()

            app.dependency_overrides[get_session] = session
            app.dependency_overrides[require_current_user] = auth
            app.dependency_overrides[require_agent_runtime_client] = service
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                headers = {'Idempotency-Key': 'same-recording'}
                thread = uuid4()
                fields = {'thread_id': str(thread)}
                first = await client.post('/v1/cry-analyses', data=fields, files={'file': ('cry.wav', wav(), 'audio/wav')}, headers=headers)
                assert first.status_code == 202, first.text
                analysis_id = first.json()['id']
                recovered = await client.get('/v1/cry-analyses/by-key/same-recording')
                assert recovered.status_code == 200 and recovered.json()['id'] == analysis_id
                listing = await client.get('/v1/cry-analyses', params=fields)
                assert [item['id'] for item in listing.json()['items']] == [analysis_id]
                other_thread_list = await client.get('/v1/cry-analyses', params={'thread_id': str(uuid4())})
                assert other_thread_list.json()['items'] == []
                # Existing results remain retrievable when vendor configuration is temporarily absent.
                app.state.settings = Settings(app_env='test')
                repeat = await client.post('/v1/cry-analyses', data=fields, files={'file': ('cry.wav', wav(), 'audio/wav')}, headers=headers)
                assert repeat.json()['id'] == analysis_id and len(calls) == 1
                changed = await client.post('/v1/cry-analyses', data=fields, files={'file': ('cry.wav', wav(2), 'audio/wav')}, headers=headers)
                assert changed.status_code == 409 and len(calls) == 1
                assert 'audio' not in first.json() and first.headers['cache-control'] == 'private, no-store'
                payload = {'analysis_id': analysis_id, 'actor_user_id': str(owners[0]), 'thread_id': str(thread)}
                scoped = await client.post('/v1/internal/agent/cry-analyses/resolve', json=payload)
                assert scoped.status_code == 200 and scoped.json()['answer'] == answer
                assert scoped.json()['reason_code'] == ('no_valid_cry_patterns' if answer == 'no_cry' else 'cry_translated')
                assert first.json()['reason_code'] is None  # 202 is still pending.
                assert recovered.json()['reason_code'] == scoped.json()['reason_code']
                other_thread = await client.post('/v1/internal/agent/cry-analyses/resolve', json={**payload, 'thread_id': str(uuid4())})
                assert other_thread.status_code == 404
                active_owner[0] = owners[1]
                hidden = await client.get(f'/v1/cry-analyses/{analysis_id}')
                assert hidden.status_code == 404
                hidden_key = await client.get('/v1/cry-analyses/by-key/same-recording')
                assert hidden_key.status_code == 404
                hidden_list = await client.get('/v1/cry-analyses', params=fields)
                assert hidden_list.json()['items'] == []
                other_owner = await client.post('/v1/internal/agent/cry-analyses/resolve', json={**payload, 'actor_user_id': str(owners[1])})
                assert other_owner.status_code == 404

    asyncio.run(run())


@postgres
def test_unconfigured_service_rejects_upload_without_leaving_pending_result() -> None:
    async def run() -> None:
        async with database() as (sessions, _, _, _, owners, _):
            app = create_app(Settings(app_env='test'))

            async def auth():
                return CurrentUser(user_id=owners[0], subject=str(owners[0]),
                                   session_id='session', token_id='token',
                                   roles=frozenset({'user'}), permissions=frozenset())

            async def session():
                async with sessions() as db:
                    try:
                        yield db
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise

            app.dependency_overrides[get_session] = session
            app.dependency_overrides[require_current_user] = auth
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                upload = await client.post('/v1/cry-analyses',
                    files={'file': ('cry.wav', wav(), 'audio/wav')},
                    headers={'Idempotency-Key': 'unconfigured-service'})
                assert upload.status_code == 503
                assert upload.json()['error']['code'] == 'cry_service_unconfigured'
                recovered = await client.get('/v1/cry-analyses/by-key/unconfigured-service')
                assert recovered.status_code == 404

    asyncio.run(run())


@postgres
def test_pending_result_can_be_recovered_by_key_without_holding_the_database(monkeypatch) -> None:
    async def run() -> None:
        async with database() as (sessions, _, _, _, owners, _):
            entered, release = asyncio.Event(), asyncio.Event()

            async def classify(body, owner, *, endpoint, api_key):
                entered.set()
                await release.wait()
                return {'status': 'done', 'answer': 'hungry'}

            monkeypatch.setattr(cry_router, 'analyze', classify)
            app = create_app(Settings(app_env='test', zoundream_endpoint='https://vendor.example/analyze', zoundream_api_key='synthetic'))

            async def auth():
                return CurrentUser(user_id=owners[0], subject=str(owners[0]),
                                   session_id='session', token_id='token', roles=frozenset({'user'}), permissions=frozenset())

            async def session():
                async with sessions() as db:
                    try:
                        yield db
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise

            app.dependency_overrides[get_session] = session
            app.dependency_overrides[require_current_user] = auth
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                headers = {'Idempotency-Key': 'recoverable-upload'}
                upload = asyncio.create_task(client.post('/v1/cry-analyses',
                    files={'file': ('cry.wav', wav(), 'audio/wav')}, headers=headers))
                await asyncio.wait_for(entered.wait(), timeout=5)
                pending = await asyncio.wait_for(client.get('/v1/cry-analyses/by-key/recoverable-upload'), timeout=5)
                assert pending.status_code == 200 and pending.json()['status'] == 'pending'
                replay = await asyncio.wait_for(client.post('/v1/cry-analyses',
                    files={'file': ('cry.wav', wav(), 'audio/wav')}, headers=headers), timeout=5)
                assert replay.status_code == 202 and replay.json()['id'] == pending.json()['id']
                release.set()
                assert (await upload).status_code == 202
                complete = await client.get('/v1/cry-analyses/by-key/recoverable-upload')
                assert complete.json()['status'] == 'done' and complete.json()['answer'] == 'hungry'

    asyncio.run(run())


@postgres
def test_new_jobs_are_admitted_with_per_owner_and_regional_capacity(monkeypatch) -> None:
    async def run() -> None:
        async with database() as (sessions, _, _, _, owners, _):
            entered = asyncio.Event()
            release = asyncio.Event()
            started: list[object] = []
            active_owner = [owners[0]]

            async def classify(body, owner, *, endpoint, api_key):
                started.append(owner)
                entered.set()
                await release.wait()
                return {'status': 'incomplete'}

            monkeypatch.setattr(cry_router, 'analyze', classify)
            monkeypatch.setattr(cry_router, 'MAX_ACTIVE_CRY_ANALYSES_PER_OWNER', 1, raising=False)
            monkeypatch.setattr(cry_router, 'MAX_ACTIVE_CRY_ANALYSES', 2, raising=False)
            app = create_app(Settings(app_env='test', zoundream_endpoint='https://vendor.example/analyze', zoundream_api_key='synthetic'))

            async def auth():
                owner = active_owner[0]
                return CurrentUser(user_id=owner, subject=str(owner), session_id='session',
                                   token_id='token', roles=frozenset({'user'}), permissions=frozenset())

            async def session():
                async with sessions() as db:
                    try:
                        yield db
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise

            app.dependency_overrides[get_session] = session
            app.dependency_overrides[require_current_user] = auth
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                def post(key: str):
                    return client.post('/v1/cry-analyses', files={'file': ('cry.wav', wav(), 'audio/wav')},
                                       headers={'Idempotency-Key': key})

                first = asyncio.create_task(post('first'))
                second_owner = None
                try:
                    await asyncio.wait_for(entered.wait(), timeout=5)
                    same_owner = await asyncio.wait_for(post('second'), timeout=5)
                    assert same_owner.status_code == 429 and same_owner.json()['error']['code'] == 'cry_analysis_busy'
                    active_owner[0] = owners[1]
                    entered.clear()
                    second_owner = asyncio.create_task(post('other-owner'))
                    await asyncio.wait_for(entered.wait(), timeout=5)
                    regional = await asyncio.wait_for(post('third'), timeout=5)
                    assert regional.status_code == 429 and regional.json()['error']['code'] == 'cry_analysis_busy'
                finally:
                    release.set()
                    await asyncio.gather(first, *( [second_owner] if second_owner is not None else [] ), return_exceptions=True)
                assert first.result().status_code == 202
                assert second_owner is not None and second_owner.result().status_code == 202
                assert len(started) == 2

    asyncio.run(run())
