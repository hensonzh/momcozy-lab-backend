from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import UUID

import httpx

from ...core.errors import ApiError
from .rules import prepare_model_image

ReviewStatus = Literal["passed", "rejected", "inconclusive", "failed"]


@dataclass(frozen=True)
class ReviewOutcome:
    status: ReviewStatus
    reason_codes: tuple[str, ...]
    policy_version: str


class ReviewProvider(Protocol):
    async def review(self, image: bytes) -> ReviewOutcome: ...


class DisabledReviewProvider:
    async def review(self, image: bytes) -> ReviewOutcome:
        del image
        return ReviewOutcome("failed", ("review_unavailable",), "disabled-v1")


class DecisionsReviewProvider:
    """Strict suitability/privacy policy over bounded Decisions probabilities."""

    QUESTIONS = (
        ("stool_visible", "Does this image clearly contain baby stool? Do not make a medical judgement."),
        ("color_shape_visible", "Are the stool color and physical form both clear enough to describe visually?"),
        ("adequate_lighting", "Is lighting sufficiently neutral to judge visible color, without heavy filter, color cast or glare?"),
        ("face_visible", "Is a child's face visible in this image?"),
        ("private_parts_visible", "Are any child's private parts visible in this image?"),
        ("sensitive_info_visible", "Is personally identifying text or other unnecessary sensitive information visible?"),
    )

    def __init__(
        self,
        *,
        post: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        model: str = "gpt-6-sol",
        pass_threshold: float = 0.85,
        reject_threshold: float = 0.80,
        policy_version: str = "stool-photo-review-v1",
    ) -> None:
        if not model or not 0.5 < pass_threshold <= 1 or not 0.5 < reject_threshold <= 1:
            raise ValueError("Invalid review policy")
        self.post = post
        self.model = model
        self.pass_threshold = pass_threshold
        self.reject_threshold = reject_threshold
        self.policy_version = policy_version

    async def review(self, image: bytes) -> ReviewOutcome:
        payload = {
            "model": self.model,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": "Assess photo suitability and privacy only; do not diagnose."},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{base64.b64encode(image).decode('ascii')}"},
            ]}],
            "questions": [
                {"type": "predicate", "name": name, "instructions": instructions}
                for name, instructions in self.QUESTIONS
            ],
        }
        response = await self.post(payload)
        answers = response.get("answers")
        if not isinstance(answers, list):
            raise ValueError("Invalid review response")
        by_name = {item.get("name"): item for item in answers if isinstance(item, dict)}
        if len(by_name) != len(self.QUESTIONS) or any(
            by_name.get(name, {}).get("type") != "predicate"
            or not isinstance(by_name[name].get("probability"), (float, int))
            or isinstance(by_name[name]["probability"], bool)
            or not 0 <= by_name[name]["probability"] <= 1
            for name, _ in self.QUESTIONS
        ):
            raise ValueError("Invalid review response")
        probability = {name: float(by_name[name]["probability"]) for name, _ in self.QUESTIONS}
        positive = {
            "stool_visible": "not_stool",
            "color_shape_visible": "color_shape_unclear",
            "adequate_lighting": "lighting_abnormal",
        }
        negative = {
            "face_visible": "face_visible",
            "private_parts_visible": "private_parts_visible",
            "sensitive_info_visible": "sensitive_info_visible",
        }
        rejected = [reason for name, reason in positive.items()
                    if probability[name] <= 1 - self.reject_threshold]
        rejected.extend(reason for name, reason in negative.items()
                        if probability[name] >= self.reject_threshold)
        if rejected:
            return ReviewOutcome("rejected", tuple(rejected), self.policy_version)
        if (all(probability[name] >= self.pass_threshold for name in positive)
                and all(probability[name] <= 1 - self.pass_threshold for name in negative)):
            return ReviewOutcome("passed", (), self.policy_version)
        return ReviewOutcome("inconclusive", ("review_uncertain",), self.policy_version)


async def post_decisions(payload: dict[str, Any], *, api_key: str, timeout: float = 20.0) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            "https://api.openai.com/v1/decisions", json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Invalid review response")
        return result


async def review_photo(body: bytes, provider: ReviewProvider) -> ReviewOutcome:
    try:
        derived = prepare_model_image(body)
        result = await provider.review(derived)
        if result.status not in {"passed", "rejected", "inconclusive", "failed"}:
            raise ValueError("Unknown review status")
        return result
    except Exception:
        # Never fail open on corrupt storage, model timeout, policy mismatch or provider errors.
        return ReviewOutcome("failed", ("review_unavailable",), "failed-v1")


def require_approved_photos(photos: Sequence[Any], owner: UUID, *, now: datetime | None = None) -> tuple[Any, ...]:
    if not 1 <= len(photos) <= 3 or len({p.id for p in photos}) != len(photos):
        raise ApiError(code="stool_photos_invalid_selection", message="Select 1 to 3 distinct photos.", status=422)
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    for photo in photos:
        expiry = getattr(photo, "review_expires_at", None)
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if (
            photo.owner_user_id != owner or photo.deleted_at is not None
            or getattr(photo, "purged_at", None) is not None
            or photo.review_status != "passed" or expiry is None or expiry <= at
            or getattr(photo, "review_sha256", None) != photo.sha256
        ):
            raise ApiError(code="stool_photos_not_approved", message="Every photo must have a current approved review.", status=409)
    return tuple(photos)
