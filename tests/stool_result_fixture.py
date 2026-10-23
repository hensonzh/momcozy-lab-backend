def valid_stool_result(*, language: str = "en") -> dict:
    summary = "Common yellow mushy stool appearance." if language == "en" else "从照片看为常见黄色糊状外观。"
    advice = "Continue observing the baby's overall condition and later stools." if language == "en" else "继续观察宝宝整体状态和后续排便变化。"
    return {
        "schema_version": "stool.result.v1",
        "rule_version": "stool.medical.rules.2026-10-23.v1",
        "assessment_status": "assessed",
        "color": "yellow",
        "consistency": "mushy",
        "findings": ["none"],
        "risk_level": "routine",
        "summary": summary,
        "care_advice": advice,
        "uncertainties": [],
        "photo_observations": [{
            "position": 0,
            "image_quality": "adequate",
            "color": "yellow",
            "consistency": "mushy",
            "findings": ["none"],
            "note": "The stool is clearly visible.",
        }],
    }
