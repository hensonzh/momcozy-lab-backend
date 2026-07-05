from __future__ import annotations

from dataclasses import dataclass


SAFETY_RESPONSE_TEMPLATE_VERSION = "safety-response.v1"


@dataclass(frozen=True)
class SafetyResponseTemplate:
    key: str
    version: str
    category: str
    handoff_type: str
    display_intent: str


SAFETY_RESPONSE_TEMPLATES: dict[str, SafetyResponseTemplate] = {
    "emotional_crisis_escalation": SafetyResponseTemplate(
        key="emotional_crisis_escalation",
        version=SAFETY_RESPONSE_TEMPLATE_VERSION,
        category="emotional_crisis",
        handoff_type="crisis_support",
        display_intent="Escalate to crisis or emergency support and stop normal product flows.",
    ),
    "maternal_infant_health_escalation": SafetyResponseTemplate(
        key="maternal_infant_health_escalation",
        version=SAFETY_RESPONSE_TEMPLATE_VERSION,
        category="health_red_flag",
        handoff_type="medical_or_emergency_support",
        display_intent="Escalate maternal or infant health red flags without diagnosis.",
    ),
    "security_refusal": SafetyResponseTemplate(
        key="security_refusal",
        version=SAFETY_RESPONSE_TEMPLATE_VERSION,
        category="prompt_injection",
        handoff_type="none",
        display_intent="Refuse prompt-injection or hidden-instruction extraction attempts.",
    ),
    "none": SafetyResponseTemplate(
        key="none",
        version=SAFETY_RESPONSE_TEMPLATE_VERSION,
        category="none",
        handoff_type="none",
        display_intent="No safety response template is needed.",
    ),
}


def get_safety_response_template(key: str) -> SafetyResponseTemplate:
    return SAFETY_RESPONSE_TEMPLATES[key]
