from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..core.errors import ApiError
from .care_report_contract import ReportGenerationInput, ReportGenerationResult
from .care_report_sources_contract import ReportSourceQuery, ReportSourcesRead

ReadModel = TypeVar('ReadModel', bound=BaseModel)


class CareReportGateway(Protocol):
    async def sources(self, query: ReportSourceQuery) -> ReportSourcesRead: ...
    async def generate(self, body: ReportGenerationInput) -> ReportGenerationResult: ...


class RuntimeCareReportGateway:
    def __init__(self, client: httpx.AsyncClient, origin: str, key: str) -> None:
        self.client, self.origin, self.key = client, origin.rstrip('/'), key

    async def sources(self, query: ReportSourceQuery) -> ReportSourcesRead:
        return await self._post('sources', query, ReportSourcesRead, timeout=10)

    async def generate(self, body: ReportGenerationInput) -> ReportGenerationResult:
        result = await self._post('generate', body, ReportGenerationResult, timeout=130)
        if result.input_hash != body.input_hash():
            raise ApiError(code='care_report_invalid_output', message='The report does not match its source snapshot.', status=502)
        try:
            result.content.validate_evidence(body.sources)
        except ValueError as error:
            raise ApiError(code='care_report_invalid_output', message='The report contains an invalid source reference.', status=502) from error
        return result

    async def _post(self, path: str, body: BaseModel, model: type[ReadModel], *, timeout: int) -> ReadModel:
        if not self.origin or not self.key:
            raise ApiError(code='care_reports_unavailable', message='AI reports are not configured.', status=503)
        try:
            async with self.client.stream('POST', f'{self.origin}/v1/internal/care-reports/{path}',
                json=body.model_dump(mode='json'), headers={'X-Service-Key': self.key}, follow_redirects=False, timeout=timeout) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 256 * 1024:
                        raise ApiError(code='care_report_invalid_output', message='The report response exceeded its limit.', status=502)
                if response.status_code != 200:
                    error_code = 'care_report_provider_unavailable'
                    try:
                        import json
                        payload: Any = json.loads(data)
                        reported = payload.get('error', {}).get('code')
                        if reported in {'care_report_refused', 'care_report_invalid_output', 'care_report_incomplete', 'care_reports_unavailable', 'care_report_timeout'}:
                            error_code = reported
                    except (ValueError, AttributeError, TypeError):
                        pass
                    raise ApiError(code=error_code, message='The report service could not complete this request.', status=503 if response.status_code >= 500 else 502)
                return model.model_validate_json(data)
        except httpx.HTTPError as error:
            raise ApiError(code='care_report_provider_unavailable', message='The report service could not be reached.', status=503) from error
        except ValidationError as error:
            raise ApiError(code='care_report_invalid_output', message='The report service returned an invalid response.', status=502) from error
