import asyncio
import base64
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.stool_specialty.analyzer import OpenAIStoolAnalyzer, SYSTEM_PROMPT
from services.stool_specialty.contracts import AnalysisInput, StoolAnalysisResult


class FakeClient:
    def __init__(self, result):
        self.responses = self
        self.result = result
        self.client_kwargs = None
        self.parse_kwargs = None

    def factory(self, **kwargs):
        self.client_kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def parse(self, **kwargs):
        self.parse_kwargs = kwargs
        return SimpleNamespace(status="completed", output_parsed=self.result)


def payload():
    return AnalysisInput.model_validate({
        "analysis_id": str(uuid4()), "market": "US", "language": "en",
        "baby_context": {
            "baby_context_snapshot_id": str(uuid4()), "profile_version": 1,
            "birth_date": None, "feeding_mode": "formula",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
        "photos": [{
            "photo_id": str(uuid4()), "position": 0, "original_sha256": "a" * 64,
            "jpeg_b64": base64.b64encode(b"\xff\xd8\xffprivate-photo").decode(),
        }],
    })


def result():
    return StoolAnalysisResult.model_validate({
        "assessment_status": "assessed", "color": "brown", "consistency": "formed",
        "findings": ["none"], "risk_level": "routine", "summary": "Visible brown formed stool.",
        "care_advice": "Continue routine observation.", "uncertainties": [],
        "photo_observations": [{"position": 0, "image_quality": "adequate", "color": "brown",
            "consistency": "formed", "findings": ["none"], "note": "The stool is visible."}],
    })


def test_lightweight_model_uses_private_structured_responses_request():
    fake = FakeClient(result())
    analyzer = OpenAIStoolAnalyzer(
        api_key="test-key", model="gpt-5.4-mini", timeout_seconds=3,
        client_factory=fake.factory,
    )
    output = asyncio.run(analyzer.analyze(payload()))
    assert output.risk_level == "routine"
    assert fake.client_kwargs == {"api_key": "test-key", "max_retries": 0, "timeout": 3}
    assert fake.parse_kwargs["model"] == "gpt-5.4-mini"
    assert fake.parse_kwargs["instructions"] == SYSTEM_PROMPT
    assert fake.parse_kwargs["text_format"] is StoolAnalysisResult
    assert fake.parse_kwargs["store"] is False
    assert fake.parse_kwargs["input"][0]["content"][1]["type"] == "input_image"
    assert fake.parse_kwargs["input"][0]["content"][1]["detail"] == "high"
    assert "owner" not in str(fake.parse_kwargs).lower()


def test_analyzer_rejects_photo_count_mismatch():
    invalid = result().model_copy(update={"photo_observations": []})
    fake = FakeClient(invalid)
    analyzer = OpenAIStoolAnalyzer(api_key="test-key", client_factory=fake.factory)
    with pytest.raises(RuntimeError, match="invalid|omitted"):
        asyncio.run(analyzer.analyze(payload()))


def test_documented_system_prompt_matches_runtime_source():
    from pathlib import Path
    markdown = (Path(__file__).resolve().parents[1] / "docs/stool-specialty-system-prompt.md").read_text()
    start = markdown.index("```text\n") + len("```text\n")
    end = markdown.index("\n```", start)
    assert markdown[start:end].strip() == SYSTEM_PROMPT
