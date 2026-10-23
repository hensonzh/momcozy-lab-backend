"""Visual-model adapter for the stateless stool specialty service."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

from .contracts import AnalysisInput, StoolAnalysisResult, StoolVisualAssessment
from .medical_rules import apply_medical_rules, medical_rule_context

SYSTEM_PROMPT = Path(__file__).with_name("system_prompt.txt").read_text(encoding="utf-8").strip()
MAX_OUTPUT_TOKENS = 2200


class StoolAnalyzer(Protocol):
    async def analyze(self, payload: AnalysisInput) -> StoolAnalysisResult: ...


class OpenAIStoolAnalyzer:
    """One private Responses request; no provider response or image is stored here."""

    def __init__(
        self, *, api_key: str, model: str = "gpt-6-sol", timeout_seconds: float = 35,
        client_factory: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client_factory = client_factory

    async def analyze(self, payload: AnalysisInput) -> StoolAnalysisResult:
        if not self.api_key or not self.model or self.timeout_seconds <= 0:
            raise RuntimeError("Stool analyzer is not configured")
        context = {
            "analysis_schema_version": payload.schema_version,
            "market": payload.market,
            "language": payload.language,
            "baby_context": payload.baby_context.model_dump(mode="json"),
            "photo_count": len(payload.photos),
            "medical_rules": medical_rule_context(market=payload.market, language=payload.language),
            "instruction": "Analyze the ordered images and return only structured visual observations.",
        }
        content: list[dict[str, Any]] = [{
            "type": "input_text",
            "text": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        }]
        content.extend({
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{photo.jpeg_b64}",
            "detail": "high",
        } for photo in payload.photos)
        try:
            if self.client_factory is None:
                from openai import AsyncOpenAI
                factory: Any = AsyncOpenAI
            else:
                factory = self.client_factory
            client = factory(api_key=self.api_key, max_retries=0, timeout=self.timeout_seconds)
            async with client as openai_client:
                response = await asyncio.wait_for(
                    openai_client.responses.parse(
                        model=self.model,
                        reasoning={"effort": "low"},
                        instructions=SYSTEM_PROMPT,
                        input=[{"role": "user", "content": content}],
                        text_format=StoolVisualAssessment,
                        store=False,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                    ),
                    timeout=self.timeout_seconds,
                )
        except Exception as exc:
            raise RuntimeError("Stool model request failed") from exc
        if str(getattr(response, "status", "completed") or "") != "completed":
            raise RuntimeError("Stool model response incomplete")
        try:
            assessment = StoolVisualAssessment.model_validate(getattr(response, "output_parsed", None))
            result = apply_medical_rules(payload, assessment)
        except Exception as exc:
            raise RuntimeError("Stool model response invalid") from exc
        if len(result.photo_observations) != len(payload.photos):
            raise RuntimeError("Stool model omitted a photo observation")
        return result
