"""Versioned stateless transport and structured stool-analysis result contract."""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
from typing import Any, Literal, TypeAlias, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PositiveInt, model_validator

StoolColor: TypeAlias = Literal[
    "yellow", "yellow_brown", "brown", "green", "dark_green", "red", "black",
    "pale_gray_white", "mixed", "unknown",
]
StoolConsistency: TypeAlias = Literal[
    "watery", "loose", "mushy", "formed", "hard_pellets", "sticky_tarry", "unknown",
]
StoolFinding: TypeAlias = Literal[
    "seedy", "mucus", "food_particles", "suspected_blood",
    "suspected_pale_acholic_stool", "suspected_tarry_black_after_meconium",
    "uncertain", "none",
]
StoolRiskLevel: TypeAlias = Literal[
    "routine", "monitor", "contact_pediatrician", "urgent",
]
StoolField: TypeAlias = Literal[
    "color", "consistency", "findings", "risk_level", "summary", "care_advice",
]

_COLORS = {"yellow", "yellow_brown", "brown", "green", "dark_green", "red", "black", "pale_gray_white", "mixed", "unknown"}
_CONSISTENCIES = {"watery", "loose", "mushy", "formed", "hard_pellets", "sticky_tarry", "unknown"}
_FINDINGS = {"seedy", "mucus", "food_particles", "suspected_blood", "suspected_pale_acholic_stool", "suspected_tarry_black_after_meconium", "uncertain", "none"}

_HIGH_FINDINGS = {
    "suspected_blood", "suspected_pale_acholic_stool",
    "suspected_tarry_black_after_meconium",
}
_RISK_ORDER = {"routine": 0, "monitor": 1, "contact_pediatrician": 2, "urgent": 3}
_FIELD_SEQUENCE: dict[int, StoolField] = {
    3: "color", 4: "consistency", 5: "findings", 6: "risk_level",
    7: "summary", 8: "care_advice",
}


class BabyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    baby_context_snapshot_id: UUID
    profile_version: int = Field(ge=1)
    birth_date: date | None
    feeding_mode: str = Field(min_length=1, max_length=32)
    captured_at: datetime


class PhotoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    photo_id: UUID
    position: int = Field(ge=0, le=2)
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    jpeg_b64: str = Field(max_length=3_000_000)

    @model_validator(mode="after")
    def validate_jpeg(self) -> PhotoInput:
        try:
            body = base64.b64decode(self.jpeg_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid JPEG encoding") from exc
        if not body.startswith(b"\xff\xd8\xff") or len(body) > 2_000_000:
            raise ValueError("Invalid or oversized derived JPEG")
        return self


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["stool.analyze.v1"] = "stool.analyze.v1"
    analysis_id: UUID
    market: Literal["CN", "US"]
    language: Literal["zh", "en"]
    baby_context: BabyContext
    photos: tuple[PhotoInput, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def verify_photos(self) -> AnalysisInput:
        if (len({photo.photo_id for photo in self.photos}) != len(self.photos)
                or [photo.position for photo in self.photos] != list(range(len(self.photos)))):
            raise ValueError("Photos must be distinct and ordered from zero")
        if self.baby_context.captured_at.tzinfo is None:
            raise ValueError("Context timestamp must be timezone aware")
        return self


class PhotoObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    position: int = Field(ge=0, le=2)
    image_quality: Literal["adequate", "limited", "unusable"]
    color: StoolColor
    consistency: StoolConsistency
    findings: list[StoolFinding] = Field(min_length=1, max_length=7)
    note: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_findings(self) -> PhotoObservation:
        if len(set(self.findings)) != len(self.findings):
            raise ValueError("Duplicate findings")
        if "none" in self.findings and len(self.findings) != 1:
            raise ValueError("none cannot be combined with other findings")
        return self


class StoolAnalysisResult(BaseModel):
    """Candidate result. Product validates it again before publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["stool.result.v1"] = "stool.result.v1"
    assessment_status: Literal["assessed", "unable_to_assess"]
    color: StoolColor
    consistency: StoolConsistency
    findings: list[StoolFinding] = Field(min_length=1, max_length=7)
    risk_level: StoolRiskLevel | None
    summary: str = Field(min_length=1, max_length=500)
    care_advice: str = Field(min_length=1, max_length=800)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)
    photo_observations: list[PhotoObservation] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_medical_consistency(self) -> StoolAnalysisResult:
        if len(set(self.findings)) != len(self.findings):
            raise ValueError("Duplicate findings")
        if "none" in self.findings and len(self.findings) != 1:
            raise ValueError("none cannot be combined with other findings")
        positions = [item.position for item in self.photo_observations]
        if positions != list(range(len(positions))):
            raise ValueError("Photo observations must be complete and ordered")
        if self.assessment_status == "unable_to_assess":
            if self.risk_level is not None or not self.uncertainties:
                raise ValueError("Unable assessment requires uncertainty and no risk level")
        elif self.risk_level is None:
            raise ValueError("Assessed result requires a risk level")
        observed_findings = {
            finding for observation in self.photo_observations for finding in observation.findings
        }
        if not (_HIGH_FINDINGS & observed_findings).issubset(set(self.findings)):
            raise ValueError("Per-photo safety findings must be preserved in the overall result")
        if _HIGH_FINDINGS.intersection(self.findings) and self.assessment_status == "assessed":
            if self.risk_level is None or _RISK_ORDER[self.risk_level] < _RISK_ORDER["contact_pediatrician"]:
                raise ValueError("Safety findings cannot be downgraded")
        for observation in self.photo_observations:
            if (observation.color == "red"
                    and not ({"suspected_blood", "uncertain"} & set(observation.findings))):
                raise ValueError("Red photo observation requires blood or uncertainty")
            if (observation.color == "pale_gray_white"
                    and "suspected_pale_acholic_stool" not in observation.findings):
                raise ValueError("Pale photo observation requires the safety finding")
            if (observation.consistency == "sticky_tarry"
                    and "suspected_tarry_black_after_meconium" not in observation.findings):
                raise ValueError("Tarry photo observation requires the safety finding")
        if all(item.image_quality == "unusable" for item in self.photo_observations)                 and self.assessment_status != "unable_to_assess":
            raise ValueError("Unusable photos cannot produce an assessed result")
        if self.color == "red" and not ({"suspected_blood", "uncertain"} & set(self.findings)):
            raise ValueError("Red stool requires blood or uncertainty finding")
        if self.color == "pale_gray_white" and "suspected_pale_acholic_stool" not in self.findings:
            raise ValueError("Pale stool requires the corresponding safety finding")
        if self.consistency == "sticky_tarry" and "suspected_tarry_black_after_meconium" not in self.findings:
            raise ValueError("Tarry stool requires the corresponding safety finding")
        return self


def result_field_value(result: StoolAnalysisResult, field: StoolField) -> JsonValue:
    value: Any = getattr(result, field)
    if isinstance(value, tuple):
        value = list(value)
    return cast(JsonValue, value)


class AnalysisEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["stool.events.v1"] = "stool.events.v1"
    analysis_id: UUID
    sequence: PositiveInt
    event_type: Literal[
        "stool.card.created", "stool.analysis.started", "stool.field.ready",
        "stool.analysis.completed", "stool.analysis.failed", "stool.analysis.cancelled",
    ]
    payload: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_event(self) -> AnalysisEvent:
        if self.event_type == "stool.card.created":
            if self.sequence != 1 or self.payload is not None:
                raise ValueError("Invalid card event")
            return self
        if self.event_type == "stool.analysis.cancelled":
            if self.sequence < 2 or self.payload is not None:
                raise ValueError("Invalid cancelled event")
            return self
        if self.event_type == "stool.analysis.started":
            if self.sequence != 2 or self.payload is not None:
                raise ValueError("Invalid started event")
            return self
        if self.event_type == "stool.analysis.failed":
            if (not 2 <= self.sequence <= 9
                    or self.payload not in (None, {"code": "analysis_failed"})):
                raise ValueError("Invalid failed event")
            return self
        if self.event_type == "stool.field.ready":
            expected = _FIELD_SEQUENCE.get(self.sequence)
            if (expected is None or self.payload is None
                    or set(self.payload) != {"field", "value"}
                    or self.payload.get("field") != expected):
                raise ValueError("Invalid field event")
            _validate_field_value(expected, self.payload.get("value"))
            return self
        if self.sequence != 9 or self.payload is None or set(self.payload) != {"result"}:
            raise ValueError("Invalid completed event")
        StoolAnalysisResult.model_validate(self.payload["result"])
        return self


def _validate_field_value(field: StoolField, value: JsonValue) -> None:
    if field == "color" and value not in _COLORS:
        raise ValueError("Invalid color field")
    if field == "consistency" and value not in _CONSISTENCIES:
        raise ValueError("Invalid consistency field")
    if field == "findings":
        if not isinstance(value, list) or not value:
            raise ValueError("Invalid findings field")
        if any(item not in _FINDINGS for item in value):
            raise ValueError("Invalid findings field")
        PhotoObservation(position=0, image_quality="adequate", color="unknown", consistency="unknown",
                         findings=cast(list[StoolFinding], value), note="validated")
    if field == "risk_level" and value is not None and value not in _RISK_ORDER:
        raise ValueError("Invalid risk field")
    if field == "summary" and (not isinstance(value, str) or not 1 <= len(value) <= 500):
        raise ValueError("Invalid summary field")
    if field == "care_advice" and (not isinstance(value, str) or not 1 <= len(value) <= 800):
        raise ValueError("Invalid care advice field")


def build_success_events(analysis_id: UUID, result: StoolAnalysisResult) -> tuple[AnalysisEvent, ...]:
    events: list[AnalysisEvent] = [AnalysisEvent(
        analysis_id=analysis_id, sequence=2, event_type="stool.analysis.started",
    )]
    for sequence, field in _FIELD_SEQUENCE.items():
        events.append(AnalysisEvent(
            analysis_id=analysis_id, sequence=sequence, event_type="stool.field.ready",
            payload={"field": field, "value": result_field_value(result, field)},
        ))
    events.append(AnalysisEvent(
        analysis_id=analysis_id, sequence=9, event_type="stool.analysis.completed",
        payload={"result": result.model_dump(mode="json")},
    ))
    return tuple(events)
