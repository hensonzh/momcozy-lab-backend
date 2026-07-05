from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ....core.metrics import RequestMetrics
from ..models import AgentSafetyEvent
from ..repository import AgentRuntimeRepository


@dataclass(frozen=True)
class SafetyDecision:
    category: str
    severity: str
    decision: str
    evidence: dict[str, object]
    response_template_key: str
    response_template_version: str
    handoff_type: str

    @property
    def should_block_normal_flow(self) -> bool:
        return self.decision in {"block", "escalate"}


class DeterministicSafetyGuard:
    def evaluate(self, text: str) -> SafetyDecision:
        normalized = _normalize(text)
        crisis_match = _first_match(normalized, EMOTIONAL_CRISIS_TERMS)
        if crisis_match:
            return SafetyDecision(
                category="emotional_crisis",
                severity="critical",
                decision="escalate",
                evidence=_evidence(crisis_match),
                response_template_key="emotional_crisis_escalation",
                response_template_version=SAFETY_RESPONSE_TEMPLATE_VERSION,
                handoff_type="crisis_support",
            )

        health_match = _first_match(normalized, HEALTH_RED_FLAG_TERMS)
        if health_match:
            return SafetyDecision(
                category="health_red_flag",
                severity="high",
                decision="escalate",
                evidence=_evidence(health_match),
                response_template_key="maternal_infant_health_escalation",
                response_template_version=SAFETY_RESPONSE_TEMPLATE_VERSION,
                handoff_type="medical_or_emergency_support",
            )

        injection_match = _first_match(normalized, PROMPT_INJECTION_TERMS)
        if injection_match:
            return SafetyDecision(
                category="prompt_injection",
                severity="medium",
                decision="block",
                evidence=_evidence(injection_match),
                response_template_key="security_refusal",
                response_template_version=SAFETY_RESPONSE_TEMPLATE_VERSION,
                handoff_type="none",
            )

        return SafetyDecision(
            category="none",
            severity="none",
            decision="allow",
            evidence={},
            response_template_key="none",
            response_template_version=SAFETY_RESPONSE_TEMPLATE_VERSION,
            handoff_type="none",
        )


class AgentSafetyService:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        guard: DeterministicSafetyGuard | None = None,
        metrics: RequestMetrics | None = None,
    ) -> None:
        self.repository = repository
        self.guard = guard or DeterministicSafetyGuard()
        self.metrics = metrics

    async def evaluate_and_record(
        self,
        *,
        owner_user_id: UUID,
        text: str,
        run_id: UUID | None = None,
    ) -> tuple[SafetyDecision, AgentSafetyEvent | None]:
        decision = self.guard.evaluate(text)
        self._record(decision)
        if decision.decision == "allow":
            return decision, None
        event = await self.repository.record_safety_event(
            run_id=run_id,
            owner_user_id=owner_user_id,
            category=decision.category,
            severity=decision.severity,
            decision=decision.decision,
            evidence=decision.evidence,
        )
        return decision, event

    def _record(self, decision: SafetyDecision) -> None:
        if self.metrics is not None:
            self.metrics.record_agent_safety(
                category=decision.category,
                decision=decision.decision,
                severity=decision.severity,
            )


SAFETY_RULE_VERSION = "safety-rules.v1"
SAFETY_RESPONSE_TEMPLATE_VERSION = "safety-response.v1"


EMOTIONAL_CRISIS_TERMS = (
    "suicide",
    "kill myself",
    "harm myself",
    "hurt myself",
    "hurt my baby",
    "harm my baby",
    "shake my baby",
    "end my life",
    "cannot go on",
    "can't go on",
    "want to die",
    "plan to die",
    "overdose",
    "自杀",
    "轻生",
    "不想活",
    "想死",
    "伤害自己",
    "伤害宝宝",
    "伤害孩子",
    "摇晃宝宝",
    "撑不下去",
)

HEALTH_RED_FLAG_TERMS = (
    "heavy bleeding",
    "bleeding a lot",
    "soaking a pad",
    "severe pain",
    "severe headache",
    "vision changes",
    "shortness of breath",
    "trouble breathing",
    "chest pain",
    "seizure",
    "fainting",
    "passed out",
    "face swelling",
    "hands swelling",
    "right upper belly pain",
    "water broke before 37 weeks",
    "baby not moving",
    "no fetal movement",
    "fever",
    "infant fever",
    "newborn fever",
    "baby fever",
    "blue lips",
    "baby has blue lips",
    "baby struggling to breathe",
    "no wet diapers",
    "breast redness",
    "worsening breast redness",
    "胎动明显减少",
    "没有胎动",
    "大出血",
    "剧烈腹痛",
    "胸痛",
    "抽搐",
    "晕倒",
    "脸肿",
    "手肿",
    "破水",
    "早破水",
    "恶露很多",
    "宝宝嘴唇发紫",
    "宝宝呼吸困难",
    "呼吸困难",
    "宝宝发烧",
    "新生儿发烧",
    "没有尿湿尿布",
    "高烧",
    "乳房红肿发热",
)

PROMPT_INJECTION_TERMS = (
    "ignore previous instructions",
    "reveal system prompt",
    "show hidden prompt",
    "bypass safety",
    "disable safety",
    "忽略之前的指令",
    "泄露系统提示词",
    "显示隐藏提示词",
)


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _first_match(text: str, terms: tuple[str, ...]) -> str:
    for term in terms:
        if term.lower() in text:
            return term
    return ""


def _evidence(matched_term: str) -> dict[str, object]:
    return {"matched_term": matched_term, "rule_version": SAFETY_RULE_VERSION}
