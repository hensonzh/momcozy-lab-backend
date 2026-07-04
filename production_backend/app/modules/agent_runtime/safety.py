from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ...core.metrics import RequestMetrics
from .models import AgentSafetyEvent
from .repository import AgentRuntimeRepository


@dataclass(frozen=True)
class SafetyDecision:
    category: str
    severity: str
    decision: str
    evidence: dict[str, object]

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
                evidence={"matched_term": crisis_match},
            )

        health_match = _first_match(normalized, HEALTH_RED_FLAG_TERMS)
        if health_match:
            return SafetyDecision(
                category="health_red_flag",
                severity="high",
                decision="escalate",
                evidence={"matched_term": health_match},
            )

        injection_match = _first_match(normalized, PROMPT_INJECTION_TERMS)
        if injection_match:
            return SafetyDecision(
                category="prompt_injection",
                severity="medium",
                decision="block",
                evidence={"matched_term": injection_match},
            )

        return SafetyDecision(category="none", severity="none", decision="allow", evidence={})


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


EMOTIONAL_CRISIS_TERMS = (
    "suicide",
    "kill myself",
    "harm myself",
    "hurt myself",
    "end my life",
    "cannot go on",
    "can't go on",
    "自杀",
    "轻生",
    "不想活",
    "伤害自己",
    "撑不下去",
)

HEALTH_RED_FLAG_TERMS = (
    "heavy bleeding",
    "bleeding a lot",
    "severe pain",
    "severe headache",
    "vision changes",
    "shortness of breath",
    "baby not moving",
    "no fetal movement",
    "fever",
    "breast redness",
    "worsening breast redness",
    "胎动明显减少",
    "没有胎动",
    "大出血",
    "剧烈腹痛",
    "高烧",
    "乳房红肿发热",
)

PROMPT_INJECTION_TERMS = (
    "ignore previous instructions",
    "reveal system prompt",
    "show hidden prompt",
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
