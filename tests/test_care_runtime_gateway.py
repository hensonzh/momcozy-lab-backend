import asyncio
from uuid import uuid4

import httpx
import pytest

from app.core.errors import ApiError
from app.core.settings import Settings
from app.infrastructure.agent_runtime import RuntimeConversationGateway


def test_runtime_owner_verification_checks_both_identifiers_and_never_follows_redirects():
    async def run():
        owner, thread = uuid4(), uuid4()
        calls = []
        response = httpx.Response(200, json={'id': str(thread), 'owner_user_id': str(owner)})
        async def handle(request):
            calls.append(request)
            return response
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            gateway = RuntimeConversationGateway(client, 'https://runtime.example.test')
            await gateway.verify_owner(owner, thread, 'Bearer synthetic-test-token')
            assert str(calls[0].url) == f'https://runtime.example.test/v1/agent/threads/{thread}'
            assert calls[0].headers['authorization'] == 'Bearer synthetic-test-token'
            response = httpx.Response(200, json={'id': str(thread), 'owner_user_id': str(uuid4())})
            with pytest.raises(ApiError) as foreign:
                await gateway.verify_owner(owner, thread, 'Bearer synthetic-test-token')
            assert foreign.value.status == 404
            response = httpx.Response(302, headers={'location': 'https://foreign.example.test'})
            with pytest.raises(ApiError) as redirect:
                await gateway.verify_owner(owner, thread, 'Bearer synthetic-test-token')
            assert redirect.value.status == 503 and len(calls) == 3
    asyncio.run(run())


def test_report_configuration_uses_a_dedicated_hidden_secret_and_known_origin(monkeypatch):
    key = 'isolated-report-service-credential-over-32-bytes'
    monkeypatch.setenv('CARE_REPORT_RUNTIME_URL', 'https://runtime.example.test/')
    monkeypatch.setenv('CARE_REPORT_SERVICE_KEY', key)
    settings = Settings.from_env()
    assert settings.care_report_runtime_url == 'https://runtime.example.test'
    assert settings.care_report_service_key == key and key not in repr(settings)
    Settings(care_report_runtime_url='http://127.0.0.1:9000', care_report_service_key=key).validate_for_startup()
    for origin in ['https://user:password@example.test', 'https://runtime.example.test/path', 'https://runtime.example.test?token=value']:
        with pytest.raises(ValueError, match='CARE_REPORT_RUNTIME_URL'):
            Settings(care_report_runtime_url=origin).validate_for_startup()
    with pytest.raises(ValueError, match='distinct'):
        Settings(care_report_service_key=key, agent_runtime_service_api_key=key).validate_for_startup()


def test_report_gateway_uses_only_dedicated_key_and_checks_source_hash_and_excerpts():
    import json
    from datetime import date, datetime, timezone
    from app.infrastructure.care_report_contract import ReportGenerationInput, ReportGenerationResult, StructuredCareReport
    from app.infrastructure.care_reports import RuntimeCareReportGateway
    async def run():
        body = ReportGenerationInput(episode_id=uuid4(), report_date=date(2026, 9, 8), timezone='UTC', as_of=datetime(2026, 9, 8, 12, tzinfo=timezone.utc),
            omitted_count=0, sources=[{'id':'lactation:synthetic:1','kind':'lactation','recorded_at':'2026-09-08T11:00:00Z','content':'左侧泵奶 60 ml。宝宝摄入量未知。'}])
        result = ReportGenerationResult(content=StructuredCareReport(summary=[{'text':'记录了左侧泵奶量。','evidence':[{'source_id':'lactation:synthetic:1','quote':'左侧泵奶 60 ml'}]}],
            emotional_state=[], communication_preferences=[], checks=[], data_gaps=[]), input_hash=body.input_hash(), provider='synthetic', model='synthetic', provider_response_id='response-test')
        payload = result.model_dump(mode='json')
        seen = []
        async def handler(request):
            seen.append(request)
            assert request.headers['X-Service-Key'] == 'synthetic-report-key'
            assert 'Authorization' not in request.headers
            assert json.loads(request.content) == body.model_dump(mode='json')
            return httpx.Response(200, json=payload)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = RuntimeCareReportGateway(client, 'https://runtime.example.test', 'synthetic-report-key')
            assert await gateway.generate(body) == result
            payload['input_hash'] = '0' * 64
            with pytest.raises(ApiError) as wrong_snapshot:
                await gateway.generate(body)
            assert wrong_snapshot.value.code == 'care_report_invalid_output'
            payload['input_hash'] = body.input_hash()
            payload['content']['summary'][0]['evidence'][0]['quote'] = '不存在的引文'
            with pytest.raises(ApiError) as invented:
                await gateway.generate(body)
            assert invented.value.code == 'care_report_invalid_output'
        assert len(seen) == 3
    asyncio.run(run())
