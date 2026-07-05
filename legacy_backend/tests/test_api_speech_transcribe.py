from __future__ import annotations

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from momcozy_agent.api_app import create_app


class _FakeTranscriptions:
    calls: list[dict] = []

    async def create(self, **kwargs):
        type(self).calls.append(kwargs)
        return SimpleNamespace(text="你好，帮我分析一下奶量")


class _FakeAudio:
    def __init__(self) -> None:
        self.transcriptions = _FakeTranscriptions()


class _FakeAsyncOpenAI:
    api_keys: list[str] = []

    def __init__(self, *, api_key: str) -> None:
        type(self).api_keys.append(api_key)
        self.audio = _FakeAudio()

    async def __aenter__(self) -> "_FakeAsyncOpenAI":
        return self

    async def __aexit__(self, exc_type, exc, exc_tb) -> None:
        return None


class SpeechTranscribeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ENTRY_API_KEY"] = "test-token"
        self.client = TestClient(create_app())
        self.headers = {"Authorization": "Bearer test-token"}
        _FakeTranscriptions.calls = []
        _FakeAsyncOpenAI.api_keys = []

    def test_transcribe_chunk_uses_openai_whisper(self) -> None:
        fake_openai = types.SimpleNamespace(AsyncOpenAI=_FakeAsyncOpenAI)
        with (
            patch.dict(sys.modules, {"openai": fake_openai}),
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "sk-test", "OPENAI_STT_MODEL": "", "OPENAI_STT_LANGUAGE": "", "OPENAI_STT_PROMPT": ""},
                clear=False,
            ),
        ):
            response = self.client.post(
                "/v1/speech/transcribe-chunk",
                data={"user_id": "mom-user-1"},
                files={"file": ("speech.wav", b"RIFF....WAVE", "audio/wav")},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": 200,
                "message": "success",
                "data": {"text": "你好，帮我分析一下奶量", "transcript": "你好，帮我分析一下奶量"},
            },
        )
        self.assertEqual(_FakeAsyncOpenAI.api_keys, ["sk-test"])
        self.assertEqual(len(_FakeTranscriptions.calls), 1)
        call = _FakeTranscriptions.calls[0]
        self.assertEqual(call["model"], "whisper-1")
        self.assertNotIn("language", call)
        self.assertNotIn("prompt", call)
        self.assertEqual(call["response_format"], "json")
        filename, body, mime_type = call["file"]
        self.assertEqual(filename, "speech.wav")
        self.assertEqual(body.read(), b"RIFF....WAVE")
        self.assertEqual(mime_type, "audio/wav")

    def test_transcribe_chunk_allows_explicit_language_override(self) -> None:
        fake_openai = types.SimpleNamespace(AsyncOpenAI=_FakeAsyncOpenAI)
        with (
            patch.dict(sys.modules, {"openai": fake_openai}),
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "sk-test", "OPENAI_STT_MODEL": "", "OPENAI_STT_LANGUAGE": "", "OPENAI_STT_PROMPT": ""},
                clear=False,
            ),
        ):
            response = self.client.post(
                "/v1/speech/transcribe-chunk",
                data={"user_id": "mom-user-1", "language": "en"},
                files={"file": ("speech.wav", b"RIFF....WAVE", "audio/wav")},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_FakeTranscriptions.calls[0]["language"], "en")

    def test_transcribe_chunk_requires_openai_api_key(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            response = self.client.post(
                "/v1/speech/transcribe-chunk",
                data={"user_id": "mom-user-1"},
                files={"file": ("speech.wav", b"RIFF....WAVE", "audio/wav")},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "openai_stt_config_missing")

    def test_transcribe_chunk_rejects_non_audio_files(self) -> None:
        response = self.client.post(
            "/v1/speech/transcribe-chunk",
            data={"user_id": "mom-user-1"},
            files={"file": ("note.txt", b"hello", "text/plain")},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalid_audio_file_type")


if __name__ == "__main__":
    unittest.main()
