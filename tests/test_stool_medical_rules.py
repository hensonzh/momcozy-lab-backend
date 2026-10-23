from datetime import datetime, timezone
from uuid import uuid4

from services.stool_specialty.contracts import AnalysisInput, PhotoObservation, StoolVisualAssessment
from services.stool_specialty.medical_rules import MEDICAL_RULE_VERSION, apply_medical_rules


def payload(language="en"):
    return AnalysisInput.model_validate({
        "analysis_id": uuid4(), "market": "US", "language": language,
        "baby_context": {
            "baby_context_snapshot_id": uuid4(), "profile_version": 1,
            "birth_date": None, "feeding_mode": "formula",
            "captured_at": datetime.now(timezone.utc),
        },
        "photos": [{
            "photo_id": uuid4(), "position": 0, "original_sha256": "a" * 64,
            "jpeg_b64": "/9j/c3ludGhldGlj",
        }],
    })


def assessment(*, finding="none", color="yellow", consistency="mushy", quality="adequate"):
    return StoolVisualAssessment(
        assessment_status="assessed", color=color, consistency=consistency,
        findings=[finding], summary="Visual observation.", uncertainties=[],
        photo_observations=[PhotoObservation(
            position=0, image_quality=quality, color=color, consistency=consistency,
            findings=[finding], note="Visible observation.",
        )],
    )


def test_versioned_rules_own_risk_and_parent_advice_not_the_model():
    result = apply_medical_rules(payload(), assessment())
    assert result.rule_version == MEDICAL_RULE_VERSION
    assert result.risk_level == "routine"
    assert result.summary.startswith("The photo shows")
    assert result.care_advice.startswith("Continue observing")


def test_visible_safety_findings_cannot_be_downgraded_and_never_invent_urgent_symptoms():
    result = apply_medical_rules(payload(), assessment(
        finding="suspected_blood", color="red",
    ))
    assert result.risk_level == "contact_pediatrician"
    assert "pediatrician" in result.care_advice.lower()
    assert result.risk_level != "urgent"


def test_unusable_or_unknown_visual_input_is_unable_to_assess_in_both_languages():
    draft = StoolVisualAssessment(
        assessment_status="unable_to_assess", color="unknown", consistency="unknown",
        findings=["uncertain"], summary="Unknown.", uncertainties=["image_quality"],
        photo_observations=[PhotoObservation(
            position=0, image_quality="unusable", color="unknown", consistency="unknown",
            findings=["uncertain"], note="Not clear.",
        )],
    )
    for language in ("en", "zh"):
        result = apply_medical_rules(payload(language), draft)
        assert result.risk_level is None
        assert result.assessment_status == "unable_to_assess"
        assert result.rule_version == MEDICAL_RULE_VERSION
        assert result.care_advice
