from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


WORKING_CONTEXT_REDIS_TTL_SECONDS = 7 * 24 * 60 * 60
EXPIRED_SKILL_GUIDANCE = (
    "This skill was loaded previously, but its instructions have been removed from context. "
    "Call load_service_skill if the current request still needs it."
)


class RedisWorkingContextClient(Protocol):
    async def get(self, key: str) -> Any: ...

    async def set(self, key: str, value: str, **kwargs: Any) -> Any: ...


class AgentWorkingContextStore(Protocol):
    async def begin_turn(
        self,
        *,
        thread_id: UUID,
        skill_ttl_turns: int,
        information_token_budget: int = 4000,
    ) -> AgentWorkingContextState: ...

    async def retain_skill(
        self,
        *,
        thread_id: UUID,
        service_skill_id: str,
        instructions: str,
        skill_ttl_turns: int,
    ) -> AgentWorkingContextState: ...

    async def retain_information(
        self,
        *,
        thread_id: UUID,
        context_key: str,
        source: str,
        information: dict[str, Any],
        guidance: str,
        ttl_turns: int,
        token_budget: int,
        invalidate_prefixes: tuple[str, ...] = (),
        priority: int = 100,
    ) -> AgentWorkingContextState: ...


@dataclass(frozen=True)
class RetainedServiceSkill:
    service_skill_id: str
    instructions: str
    loaded_turn: int
    expires_after_turn: int
    forget_after_turn: int


@dataclass(frozen=True)
class RetainedKnownInformation:
    context_key: str
    source: str
    information: dict[str, Any]
    guidance: str
    captured_turn: int
    expires_after_turn: int
    priority: int


@dataclass(frozen=True)
class AgentWorkingContextState:
    turn_index: int = 0
    skills: tuple[RetainedServiceSkill, ...] = ()
    known_information: tuple[RetainedKnownInformation, ...] = ()


class RedisAgentWorkingContextStore:
    def __init__(
        self,
        redis_client: RedisWorkingContextClient,
        *,
        redis_ttl_seconds: int = WORKING_CONTEXT_REDIS_TTL_SECONDS,
    ) -> None:
        self.redis = redis_client
        self.redis_ttl_seconds = max(60, int(redis_ttl_seconds))

    async def begin_turn(
        self,
        *,
        thread_id: UUID,
        skill_ttl_turns: int,
        information_token_budget: int = 4000,
    ) -> AgentWorkingContextState:
        state = await self._load(thread_id=thread_id)
        turn_index = state.turn_index + 1
        fallback_ttl = max(1, int(skill_ttl_turns))
        skills = tuple(
            skill
            for skill in state.skills
            if turn_index <= max(skill.forget_after_turn, skill.loaded_turn + (fallback_ttl * 2))
        )
        known_information = _trim_known_information(
            (item for item in state.known_information if turn_index <= item.expires_after_turn),
            token_budget=information_token_budget,
        )
        next_state = AgentWorkingContextState(
            turn_index=turn_index,
            skills=skills,
            known_information=known_information,
        )
        await self._save(thread_id=thread_id, state=next_state)
        return next_state

    async def retain_skill(
        self,
        *,
        thread_id: UUID,
        service_skill_id: str,
        instructions: str,
        skill_ttl_turns: int,
    ) -> AgentWorkingContextState:
        state = await self._load(thread_id=thread_id)
        ttl_turns = max(1, int(skill_ttl_turns))
        normalized_skill_id = str(service_skill_id or "").strip()
        retained = RetainedServiceSkill(
            service_skill_id=normalized_skill_id,
            instructions=str(instructions or "").strip(),
            loaded_turn=state.turn_index,
            expires_after_turn=state.turn_index + ttl_turns,
            forget_after_turn=state.turn_index + (ttl_turns * 2),
        )
        skills = tuple(skill for skill in state.skills if skill.service_skill_id != normalized_skill_id) + (retained,)
        next_state = AgentWorkingContextState(
            turn_index=state.turn_index,
            skills=skills,
            known_information=state.known_information,
        )
        await self._save(thread_id=thread_id, state=next_state)
        return next_state

    async def retain_information(
        self,
        *,
        thread_id: UUID,
        context_key: str,
        source: str,
        information: dict[str, Any],
        guidance: str,
        ttl_turns: int,
        token_budget: int,
        invalidate_prefixes: tuple[str, ...] = (),
        priority: int = 100,
    ) -> AgentWorkingContextState:
        state = await self._load(thread_id=thread_id)
        normalized_key = str(context_key or "").strip()
        normalized_source = str(source or "").strip()
        if not normalized_key or not normalized_source:
            return state
        invalidation_prefixes = tuple(str(prefix or "").strip() for prefix in invalidate_prefixes if str(prefix or "").strip())
        retained = RetainedKnownInformation(
            context_key=normalized_key,
            source=normalized_source,
            information=_bounded_information(information),
            guidance=str(guidance or "").strip(),
            captured_turn=state.turn_index,
            expires_after_turn=state.turn_index + max(1, int(ttl_turns)),
            priority=max(0, int(priority)),
        )
        known_information = (
            item
            for item in state.known_information
            if item.context_key != normalized_key
            and not any(item.context_key.startswith(prefix) for prefix in invalidation_prefixes)
        )
        next_state = AgentWorkingContextState(
            turn_index=state.turn_index,
            skills=state.skills,
            known_information=_trim_known_information((*known_information, retained), token_budget=token_budget),
        )
        await self._save(thread_id=thread_id, state=next_state)
        return next_state

    async def _load(self, *, thread_id: UUID) -> AgentWorkingContextState:
        raw = await self.redis.get(_working_context_key(thread_id))
        try:
            payload = json.loads(str(raw)) if raw else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            return AgentWorkingContextState()
        turn_index = _non_negative_int(payload.get("turn_index"))
        raw_skills = payload.get("skills")
        skills: list[RetainedServiceSkill] = []
        if isinstance(raw_skills, list):
            for item in raw_skills:
                skill = _retained_skill_from_json(item)
                if skill is not None:
                    skills.append(skill)
        raw_information = payload.get("known_information")
        known_information: list[RetainedKnownInformation] = []
        if isinstance(raw_information, list):
            for item in raw_information:
                retained_information = _retained_information_from_json(item)
                if retained_information is not None:
                    known_information.append(retained_information)
        return AgentWorkingContextState(
            turn_index=turn_index,
            skills=tuple(skills),
            known_information=tuple(known_information),
        )

    async def _save(self, *, thread_id: UUID, state: AgentWorkingContextState) -> None:
        payload = {
            "turn_index": state.turn_index,
            "skills": [
                {
                    "service_skill_id": skill.service_skill_id,
                    "instructions": skill.instructions,
                    "loaded_turn": skill.loaded_turn,
                    "expires_after_turn": skill.expires_after_turn,
                    "forget_after_turn": skill.forget_after_turn,
                }
                for skill in state.skills
            ],
            "known_information": [
                {
                    "context_key": item.context_key,
                    "source": item.source,
                    "information": item.information,
                    "guidance": item.guidance,
                    "captured_turn": item.captured_turn,
                    "expires_after_turn": item.expires_after_turn,
                    "priority": item.priority,
                }
                for item in state.known_information
            ],
        }
        await self.redis.set(
            _working_context_key(thread_id),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            ex=self.redis_ttl_seconds,
        )


def project_working_context(
    state: AgentWorkingContextState,
    *,
    ongoing_work: list[dict[str, Any]] | None = None,
    known_information: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resident = sorted(
        (skill for skill in state.skills if state.turn_index <= skill.expires_after_turn),
        key=lambda skill: (skill.loaded_turn, skill.service_skill_id),
    )
    expired = sorted(
        (skill for skill in state.skills if state.turn_index > skill.expires_after_turn),
        key=lambda skill: (skill.loaded_turn, skill.service_skill_id),
        reverse=True,
    )
    return {
        "skills": [
            *({"id": skill.service_skill_id, "instructions": skill.instructions} for skill in resident),
            *({"id": skill.service_skill_id, "guidance": EXPIRED_SKILL_GUIDANCE} for skill in expired),
        ],
        "ongoing_work": list(ongoing_work or []),
        "known_information": list(known_information) if known_information is not None else [
            {
                "source": item.source,
                "information": item.information,
                "guidance": item.guidance,
            }
            for item in state.known_information
        ],
    }


def empty_working_context_state() -> AgentWorkingContextState:
    return AgentWorkingContextState()


def _retained_skill_from_json(value: Any) -> RetainedServiceSkill | None:
    if not isinstance(value, dict):
        return None
    service_skill_id = str(value.get("service_skill_id") or "").strip()
    instructions = str(value.get("instructions") or "").strip()
    if not service_skill_id or not instructions:
        return None
    loaded_turn = _non_negative_int(value.get("loaded_turn"))
    expires_after_turn = max(loaded_turn, _non_negative_int(value.get("expires_after_turn")))
    forget_after_turn = max(expires_after_turn, _non_negative_int(value.get("forget_after_turn")))
    return RetainedServiceSkill(
        service_skill_id=service_skill_id,
        instructions=instructions,
        loaded_turn=loaded_turn,
        expires_after_turn=expires_after_turn,
        forget_after_turn=forget_after_turn,
    )


def _retained_information_from_json(value: Any) -> RetainedKnownInformation | None:
    if not isinstance(value, dict):
        return None
    context_key = str(value.get("context_key") or "").strip()
    source = str(value.get("source") or "").strip()
    information = value.get("information")
    if not context_key or not source or not isinstance(information, dict):
        return None
    return RetainedKnownInformation(
        context_key=context_key,
        source=source,
        information=_bounded_information(information),
        guidance=str(value.get("guidance") or "").strip(),
        captured_turn=_non_negative_int(value.get("captured_turn")),
        expires_after_turn=_non_negative_int(value.get("expires_after_turn")),
        priority=_non_negative_int(value.get("priority")),
    )


def _trim_known_information(
    values: Any,
    *,
    token_budget: int,
) -> tuple[RetainedKnownInformation, ...]:
    candidates = sorted(
        list(values),
        key=lambda item: (item.priority, item.captured_turn, item.context_key),
        reverse=True,
    )
    remaining = max(0, int(token_budget))
    selected: list[RetainedKnownInformation] = []
    for item in candidates:
        item_tokens = _estimated_information_tokens(item)
        if item_tokens > remaining:
            continue
        selected.append(item)
        remaining -= item_tokens
    return tuple(selected)


def _estimated_information_tokens(item: RetainedKnownInformation) -> int:
    model_value = {
        "source": item.source,
        "information": item.information,
        "guidance": item.guidance,
    }
    encoded = json.dumps(model_value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return max(1, (len(encoded) + 3) // 4)


def _bounded_information(value: dict[str, Any]) -> dict[str, Any]:
    bounded = _bounded_value(value, depth=0)
    return bounded if isinstance(bounded, dict) else {}


def _bounded_value(value: Any, *, depth: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 2000 else f"{value[:1999].rstrip()}…"
    if isinstance(value, dict):
        if depth >= 4:
            return "[nested data omitted]"
        return {
            str(key)[:80]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, (list, tuple)):
        if depth >= 4:
            return ["[nested data omitted]"] if value else []
        return [_bounded_value(item, depth=depth + 1) for item in list(value)[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _working_context_key(thread_id: UUID) -> str:
    return f"agent:thread:{thread_id}:working_context"
