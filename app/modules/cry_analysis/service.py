"""Isolated provider adapter. Only typed results, never provider prose, are persisted."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import wave
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from ...core.errors import ApiError

ANSWERS = frozenset({'sleep', 'pain', 'burp', 'uncomfortable', 'hungry', 'no_cry'})
REASON_CODES = frozenset({'cry_translated', 'no_valid_cry_patterns'})
MAX_WAV_BYTES = 1_000_000
BLOCK_BYTES = 32_000


def validate_wav(body: bytes) -> bytes:
    if not 44 <= len(body) <= MAX_WAV_BYTES or body[:4] != b'RIFF' or body[8:12] != b'WAVE' or int.from_bytes(body[4:8], 'little') + 8 != len(body):
        raise ApiError(code='cry_audio_invalid', message='Recording must be a valid WAV.', status=422)
    try:
        with wave.open(io.BytesIO(body), 'rb') as audio:
            frames = audio.getnframes()
            if (audio.getcomptype() != 'NONE' or audio.getnchannels() != 1 or audio.getsampwidth() != 2
                    or audio.getframerate() != 16000 or not 16000 <= frames <= 480000):
                raise ValueError('unexpected audio format')
            pcm = audio.readframes(frames)
            if len(pcm) != 2 * frames:
                raise ValueError('truncated audio')
            return pcm
    except (wave.Error, EOFError, ValueError) as exc:
        raise ApiError(code='cry_audio_invalid', message='Recording must be 16 kHz mono PCM, 1–30 seconds.', status=422) from exc


def normalized_result(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or value.get('phase') not in {'detecting', 'translating', 'done'}:
        raise ApiError(code='cry_service_invalid', message='Cry service returned an invalid result.', status=502)
    if value['phase'] != 'done':
        return {'status': 'incomplete'}
    reason = value.get('reason')
    if reason is not None and not isinstance(reason, str):
        raise ApiError(code='cry_service_invalid', message='Cry service returned an invalid reason code.', status=502)
    if reason in {'activation_already_closed', 'timestamp_out_of_sequence', 'activation_expired'}:
        raise ApiError(code='cry_service_invalid', message='Cry service protocol failed.', status=502)
    answer = value.get('answer')
    if answer not in ANSWERS:
        raise ApiError(code='cry_service_invalid', message='Cry service returned an unknown category.', status=502)
    if answer == 'no_cry' and value.get('reason') != 'no_valid_cry_patterns':
        return {'status': 'incomplete'}
    if reason is not None and (reason not in REASON_CODES or (reason == 'no_valid_cry_patterns') != (answer == 'no_cry')):
        raise ApiError(code='cry_service_invalid', message='Cry service returned an invalid reason code.', status=502)
    return {'status': 'done', 'answer': answer, **({'reason_code': reason} if reason else {})}


def validate_cry_service_configuration(endpoint: str, api_key: str) -> None:
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.fragment
            or not api_key or parsed.hostname in {'localhost', 'host.docker.internal'}):
        raise ApiError(code='cry_service_unconfigured', message='Cry analysis is unavailable.', status=503)
    try:
        host = ip_address(parsed.hostname or '')
    except ValueError:
        host = None
    if host is not None and not host.is_global:
        raise ApiError(code='cry_service_unconfigured', message='Cry analysis is unavailable.', status=503)


async def analyze(wav: bytes, owner: UUID, *, endpoint: str, api_key: str) -> dict[str, str]:
    validate_cry_service_configuration(endpoint, api_key)
    pcm = validate_wav(wav)
    headers = {
        'Authorization': hmac.new(api_key.encode(), str(owner).encode(), hashlib.sha256).hexdigest(),
        'x-api-key': api_key, 'User-Agent': 'Momcozy/1.0 (Product; CN)',
        'x-audio-sample-rate': '16000', 'Content-Type': 'application/octet-stream',
    }
    try:
        async with asyncio.timeout(90):
            async with httpx.AsyncClient(timeout=12) as client:
                loop = asyncio.get_running_loop()
                previous_send: float | None = None
                timestamp = 0
                saw_no_cry = False
                for offset in range(0, len(pcm), BLOCK_BYTES):
                    # Recorded WAV arrives all at once, while this API accepts
                    # one second of live audio per call. Preserve its cadence.
                    if previous_send is not None:
                        remaining = 1.0 - (loop.time() - previous_send)
                        if remaining > 0:
                            await asyncio.sleep(remaining)
                    previous_send = loop.time()
                    block = pcm[offset:offset + BLOCK_BYTES].ljust(BLOCK_BYTES, b'\0')
                    response = None
                    for attempt in range(3):
                        response = await client.post(endpoint, content=block,
                                                     headers={**headers, 'x-audio-timestamp': str(timestamp)})
                        if response.status_code not in {500, 503} or attempt == 2:
                            break
                        await asyncio.sleep(0.2 * (attempt + 1))
                    assert response is not None
                    response.raise_for_status()
                    payload = response.json()
                    result = normalized_result(payload)
                    if payload['phase'] == 'done':
                        if result.get('answer') == 'no_cry':
                            saw_no_cry = True
                        elif result['status'] == 'done':
                            return result
                        # A closed activation cannot receive timestamp +1000.
                        # Try the rest of the real recording as a new one.
                        timestamp = 0
                    else:
                        timestamp += 1000
                if saw_no_cry:
                    return {'status': 'done', 'answer': 'no_cry', 'reason_code': 'no_valid_cry_patterns'}
    except (httpx.HTTPError, ValueError, TypeError, TimeoutError) as exc:
        raise ApiError(code='cry_service_unavailable', message='Cry analysis is unavailable.', status=502) from exc
    return {'status': 'incomplete'}
