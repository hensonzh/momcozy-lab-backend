from __future__ import annotations

from typing import Any

from ..types import RuntimeInputs

CRISIS_TERMS = [
    "hurt myself",
    "kill myself",
    "suicide",
    "hurt my baby",
    "harm my baby",
    "cannot keep baby safe",
    "can't keep baby safe",
    "撑不住",
    "伤害宝宝",
    "自杀",
    "不想活",
]

EMERGENCY_TERMS = [
    "blue lips",
    "not breathing",
    "unresponsive",
    "seizure",
    "heavy bleeding",
    "chest pain",
    "呼吸困难",
    "嘴唇发紫",
    "没有反应",
    "大出血",
    "胸痛",
]


def evaluate_risk(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    message = str(args.get("message") or inputs.get("user_message") or "")
    risk = _evaluate_risk(message)
    return {
        "tool_name": "risk_evaluate",
        "risk_level": risk["risk_level"],
        "domain": args.get("domain", "general"),
        "reasons": risk["reasons"],
        "status": "evaluated",
    }


def _evaluate_risk(message: str) -> dict[str, Any]:
    normalized = message.lower()
    if any(term.lower() in normalized for term in CRISIS_TERMS):
        return {"risk_level": "crisis", "reasons": ["Detected emotional crisis language."]}
    if any(term.lower() in normalized for term in EMERGENCY_TERMS):
        return {"risk_level": "emergency", "reasons": ["Detected potential health emergency language."]}
    if "fever" in normalized or "发烧" in normalized or "红肿" in normalized:
        return {"risk_level": "medium", "reasons": ["Detected possible health risk language."]}
    return {"risk_level": "none", "reasons": ["No high-risk safety language detected by risk_evaluate."]}
