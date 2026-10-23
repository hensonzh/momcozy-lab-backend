"""Derived JPEG input, bounded context and strict structured result contract."""
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.stool_analyses.transport_contract import (
    SpecialtyAnalysisInput, SpecialtyTransportEvent, StoolAnalysisResult,
)


def job_data():
    return {
        "schema_version": "stool.analyze.v1", "analysis_id": uuid4(),
        "baby_context": {"baby_context_snapshot_id": uuid4(), "profile_version": 1, "birth_date": None,
                         "feeding_mode": "unknown", "captured_at": datetime.now(timezone.utc)},
        "market": "CN", "language": "zh",
        "photos": [{"photo_id": uuid4(), "position": 0, "original_sha256": "a" * 64,
                    "jpeg_b64": base64.b64encode(b"\xff\xd8\xffsynthetic").decode()}],
    }


def test_derived_input_has_no_original_storage_or_caller_identity():
    job = SpecialtyAnalysisInput.model_validate(job_data())
    assert job.photos[0].position == 0 and job.market == "CN"
    assert "owner_user_id" not in job.model_dump() and "object_key" not in job.model_dump_json()


@pytest.mark.parametrize("change", [
    {"photos": []}, {"photos": job_data()["photos"] * 4},
    {"photos": [{**job_data()["photos"][0], "position": 1}]},
    {"photos": [{**job_data()["photos"][0], "original_sha256": "invalid"}]},
    {"photos": [{**job_data()["photos"][0], "jpeg_b64": "not-base64"}]},
    {"language": "fr"}, {"market": "EU"}, {"owner_user_id": uuid4()},
    {"object_key": "users/private/original"},
])
def test_transport_rejects_invalid_input(change):
    with pytest.raises(ValidationError):
        SpecialtyAnalysisInput.model_validate({**job_data(), **change})


@pytest.mark.parametrize("market", ["CN", "US"])
@pytest.mark.parametrize("language", ["zh", "en"])
def test_supported_market_language(market, language):
    job = SpecialtyAnalysisInput.model_validate({**job_data(), "market": market, "language": language})
    assert (job.market, job.language) == (market, language)


def test_duplicate_photo_and_wrong_order_are_rejected():
    first = job_data()["photos"][0]
    for photos in ([first, {**first, "position": 1}], [first, {**first, "photo_id": uuid4(), "position": 2}]):
        with pytest.raises(ValidationError):
            SpecialtyAnalysisInput.model_validate({**job_data(), "photos": photos})


def test_transport_event_rejects_unbound_or_invalid_result_payloads():
    event = SpecialtyTransportEvent.model_validate({
        "schema_version": "stool.events.v1", "analysis_id": uuid4(), "sequence": 1,
        "event_type": "stool.card.created",
    })
    for bad in ({"event_type": "stool.field.ready"}, {"event_type": "stool.analysis.completed"},
                {"payload": {"risk_level": "unreviewed"}}, {"sequence": 0}):
        with pytest.raises(ValidationError):
            SpecialtyTransportEvent.model_validate({**event.model_dump(), **bad})



def result_data():
    return {
        "assessment_status": "assessed", "color": "yellow", "consistency": "mushy",
        "findings": ["none"], "risk_level": "routine", "summary": "Common appearance.",
        "care_advice": "Continue observation.", "uncertainties": [],
        "photo_observations": [{"position": 0, "image_quality": "adequate", "color": "yellow",
            "consistency": "mushy", "findings": ["none"], "note": "Clear image."}],
    }


def test_result_contract_never_downgrades_or_averages_away_safety_findings():
    with pytest.raises(ValidationError):
        StoolAnalysisResult.model_validate({
            **result_data(), "color": "red", "findings": ["suspected_blood"], "risk_level": "routine",
            "photo_observations": [{"position": 0, "image_quality": "adequate", "color": "red",
                "consistency": "mushy", "findings": ["suspected_blood"], "note": "Red material."}],
        })
    with pytest.raises(ValidationError):
        StoolAnalysisResult.model_validate({
            **result_data(),
            "photo_observations": [{"position": 0, "image_quality": "limited", "color": "pale_gray_white",
                "consistency": "mushy", "findings": ["suspected_pale_acholic_stool"], "note": "Pale."}],
        })
    unable = StoolAnalysisResult.model_validate({
        **result_data(), "assessment_status": "unable_to_assess", "color": "pale_gray_white",
        "findings": ["suspected_pale_acholic_stool"], "risk_level": None,
        "uncertainties": ["Lighting limits confidence."],
        "photo_observations": [{"position": 0, "image_quality": "limited", "color": "pale_gray_white",
            "consistency": "unknown", "findings": ["suspected_pale_acholic_stool"], "note": "Pale but uncertain."}],
    })
    assert unable.risk_level is None

def test_transport_snapshot_matches_models():
    from scripts.export_stool_specialty_transport import build_catalog
    snapshot = Path(__file__).resolve().parents[1] / "docs/contracts/stool-specialty-transport.v1.json"
    assert json.loads(snapshot.read_text()) == build_catalog()
    assert "stool.field.ready" in snapshot.read_text()
    assert "stool.analysis.completed" in snapshot.read_text()
