from uuid import UUID

import httpx

from ..core.errors import ApiError


class RuntimeConversationGateway:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self.client, self.base_url = client, base_url.rstrip('/')

    async def verify_owner(self, owner: UUID, thread: UUID, authorization: str) -> None:
        if not authorization.lower().startswith('bearer ') or not authorization.partition(' ')[2].strip():
            raise ApiError(code='authentication_required', message='Bearer authentication is required.', status=401)
        if not self.base_url:
            raise ApiError(code='care_reports_unavailable', message='Service conversations are temporarily unavailable.', status=503)
        try:
            response = await self.client.get(f'{self.base_url}/v1/agent/threads/{thread}', headers={'Authorization': authorization}, follow_redirects=False, timeout=5)
        except httpx.HTTPError as error:
            raise ApiError(code='care_runtime_unavailable', message='The conversation service could not be reached.', status=503) from error
        if response.status_code == 404:
            raise ApiError(code='not_found', message='Conversation not found.', status=404)
        if response.status_code == 401:
            raise ApiError(code='authentication_required', message='Sign in again to link the conversation.', status=401)
        if response.status_code != 200:
            raise ApiError(code='care_runtime_unavailable', message='The conversation service could not verify sharing.', status=503)
        try:
            value = response.json()
            matches = isinstance(value, dict) and UUID(value['id']) == thread and UUID(value['owner_user_id']) == owner
        except (KeyError, TypeError, ValueError):
            matches = False
        if not matches:
            raise ApiError(code='not_found', message='Conversation not found.', status=404)
