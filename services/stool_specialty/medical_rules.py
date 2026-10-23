"""Versioned deterministic medical-safety projection over model visual observations."""
from __future__ import annotations

from datetime import date
from typing import Any

from .contracts import (
    MEDICAL_RULE_VERSION, AnalysisInput, StoolAnalysisResult, StoolVisualAssessment,
)

_SAFETY_FINDINGS = {
    "suspected_blood",
    "suspected_pale_acholic_stool",
    "suspected_tarry_black_after_meconium",
}
_MONITOR_FINDINGS = {"mucus", "uncertain"}
_MONITOR_CONSISTENCIES = {"watery", "hard_pellets"}

_COLOR = {
    "en": {
        "yellow": "yellow", "yellow_brown": "yellow-brown", "brown": "brown",
        "green": "green", "dark_green": "dark green", "red": "red",
        "black": "black", "pale_gray_white": "pale gray or white",
        "mixed": "mixed-color", "unknown": "uncertain-color",
    },
    "zh": {
        "yellow": "黄色", "yellow_brown": "黄褐色", "brown": "棕色",
        "green": "绿色", "dark_green": "深绿色", "red": "红色",
        "black": "黑色", "pale_gray_white": "灰白或陶土色",
        "mixed": "混合颜色", "unknown": "颜色无法确认",
    },
}
_CONSISTENCY = {
    "en": {
        "watery": "watery", "loose": "loose", "mushy": "mushy",
        "formed": "formed", "hard_pellets": "hard pellet-like",
        "sticky_tarry": "sticky or tarry", "unknown": "uncertain-consistency",
    },
    "zh": {
        "watery": "明显水样", "loose": "松散", "mushy": "糊状",
        "formed": "成形", "hard_pellets": "干硬颗粒状",
        "sticky_tarry": "黏稠或柏油样", "unknown": "性状无法确认",
    },
}


def medical_rule_context(*, market: str, language: str) -> dict[str, Any]:
    """Bounded prompt context. The same version is persisted in every final result."""
    return {
        "version": MEDICAL_RULE_VERSION,
        "market": market,
        "language": language,
        "scope": "visual_stool_observation_for_infants_birth_through_first_birthday",
        "rules": {
            "unable_to_assess": [
                "all_photos_unusable",
                "visible_features_cannot_be_reliably_distinguished",
            ],
            "contact_pediatrician": sorted(_SAFETY_FINDINGS),
            "monitor": [
                "watery_visual_appearance_without_trusted_frequency_history",
                "hard_pellet_visual_appearance_without_trusted_pain_history",
                "mucus_or_material_visual_uncertainty",
                "limited_image_quality",
            ],
            "routine": "clear common appearance with no safety or monitor finding",
            "urgent": "not assignable without trusted urgent symptom context; current input does not contain it",
        },
        "output_boundary": (
            "Return visual observations only. The service applies risk and care advice "
            "deterministically after structured parsing."
        ),
    }


def apply_medical_rules(
    payload: AnalysisInput,
    assessment: StoolVisualAssessment,
) -> StoolAnalysisResult:
    """Create the published result without letting the model invent risk or care advice."""
    if len(assessment.photo_observations) != len(payload.photos):
        raise ValueError("Visual assessment omitted a photo observation")

    findings: set[str] = {str(item) for item in assessment.findings}
    all_unusable = all(item.image_quality == "unusable" for item in assessment.photo_observations)
    unable = assessment.assessment_status == "unable_to_assess" or all_unusable

    if unable:
        risk = None
    elif findings & _SAFETY_FINDINGS:
        risk = "contact_pediatrician"
    elif (
        findings & _MONITOR_FINDINGS
        or assessment.consistency in _MONITOR_CONSISTENCIES
        or assessment.color in {"mixed", "unknown"}
        or assessment.consistency == "unknown"
        or any(item.image_quality == "limited" for item in assessment.photo_observations)
    ):
        risk = "monitor"
    else:
        risk = "routine"

    language = payload.language
    summary = _summary(language, assessment, unable=unable)
    advice = _advice(language, findings=findings, risk=risk)
    uncertainties = list(assessment.uncertainties)
    if unable and not uncertainties:
        uncertainties.append("visual_assessment_unavailable")

    return StoolAnalysisResult.model_validate({
        **assessment.model_dump(mode="json"),
        "assessment_status": "unable_to_assess" if unable else "assessed",
        "rule_version": MEDICAL_RULE_VERSION,
        "risk_level": risk,
        "summary": summary,
        "care_advice": advice,
        "uncertainties": uncertainties,
    })


def _summary(language: str, assessment: StoolVisualAssessment, *, unable: bool) -> str:
    if unable:
        return (
            "The photo does not support a reliable stool assessment."
            if language == "en" else "当前照片不足以对便便外观作出可靠评估。"
        )
    color = _COLOR[language][assessment.color]
    consistency = _CONSISTENCY[language][assessment.consistency]
    if language == "en":
        return f"The photo shows a {color}, {consistency} stool appearance."
    return f"照片中可见{color}、{consistency}的便便外观。"


def _advice(language: str, *, findings: set[str], risk: str | None) -> str:
    if risk is None:
        return (
            "Retake a clear, neutrally lit photo. If the baby seems unwell or you remain concerned, contact a pediatrician."
            if language == "en" else
            "请在自然光下重新拍摄清晰照片；如果宝宝状态异常或你仍然担心，请联系儿科医生。"
        )
    if "suspected_pale_acholic_stool" in findings:
        return (
            "Contact a pediatrician promptly to assess the pale or clay-colored appearance."
            if language == "en" else "照片疑似灰白或陶土色便，请尽快联系儿科医生评估。"
        )
    if "suspected_blood" in findings:
        return (
            "Contact a pediatrician promptly about the visible red material or possible blood."
            if language == "en" else "照片可见红色物质或疑似血液，请尽快联系儿科医生。"
        )
    if "suspected_tarry_black_after_meconium" in findings:
        return (
            "Contact a pediatrician promptly about the black or tarry appearance outside the expected meconium stage."
            if language == "en" else "照片疑似出现非胎便阶段的黑色或柏油样便，请尽快联系儿科医生。"
        )
    if risk == "monitor":
        return (
            "Monitor the baby's overall condition and later stools. Contact a pediatrician if this persists, worsens, or new symptoms appear."
            if language == "en" else
            "请观察宝宝整体状态和后续排便；如果这种情况持续、加重或出现新症状，请联系儿科医生。"
        )
    return (
        "Continue observing the baby's overall condition and later stools. A photo alone cannot rule out illness."
        if language == "en" else
        "继续观察宝宝整体状态和后续排便；仅凭照片不能排除疾病。"
    )


def age_days(birth_date: date | None, captured_at: date) -> int | None:
    """Kept explicit for future rule expansion without inferring a timezone."""
    return None if birth_date is None else (captured_at - birth_date).days
