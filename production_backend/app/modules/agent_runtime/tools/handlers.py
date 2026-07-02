from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from ....core.errors import ApiError
from ...profiles.models import InfantProfile, UserProfile
from ...profiles.service import ProfileService
from ...support.agent_actions import SUPPORT_TICKET_CREATE_ACTION
from ..service import AgentRuntimeService
from .executor import ToolHandler, ToolHandlerContext


class ProfileReadToolHandler:
    def __init__(self, *, service: ProfileService) -> None:
        self.service = service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        profile = await self.service.get_user_profile(user_id=context.actor.user_id)
        infants = await self.service.list_infants(owner_user_id=context.actor.user_id)
        return {
            "profile": _profile_payload(profile=profile, actor_user_id=context.actor.user_id),
            "infants": [_infant_payload(infant) for infant in infants],
        }


class SupportTicketProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _support_ticket_apply_payload(context.args)
        issue_summary = _text(apply_payload, "issue_summary")
        if not issue_summary:
            raise ApiError(code="validation_failed", message="issue_summary is required.", status=422)

        preview_payload = _support_ticket_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=SUPPORT_TICKET_CREATE_ACTION,
            target_type="support_ticket",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:support-ticket",
        )
        return {
            "action_id": str(action.id),
            "action_type": action.action_type,
            "action_status": action.status,
            "requires_confirmation": True,
            "preview_payload": preview_payload,
        }


def build_default_tool_handlers(
    *,
    profile_service: ProfileService,
    agent_runtime_service: AgentRuntimeService,
) -> dict[str, ToolHandler]:
    return {
        "profile.read": ProfileReadToolHandler(service=profile_service),
        "support.ticket.propose": SupportTicketProposeToolHandler(runtime_service=agent_runtime_service),
    }


def _profile_payload(*, profile: UserProfile | None, actor_user_id: UUID) -> dict[str, Any]:
    if profile is None:
        return {
            "user_id": str(actor_user_id),
            "display_name": "",
            "age": None,
            "delivery_date": None,
            "lactation_advice": "",
            "feeding_advice": "",
            "daily_summary": "",
            "profile_onboarding_complete": False,
            "profile_onboarding_skipped": False,
        }
    return {
        "user_id": str(actor_user_id),
        "display_name": profile.display_name or "",
        "age": profile.age,
        "delivery_date": _date_iso(profile.delivery_date),
        "lactation_advice": profile.lactation_advice or "",
        "feeding_advice": profile.feeding_advice or "",
        "daily_summary": profile.daily_summary or "",
        "profile_onboarding_complete": bool(profile.profile_onboarding_completed_at or (profile.display_name and profile.age)),
        "profile_onboarding_skipped": bool(profile.profile_onboarding_skipped_at),
    }


def _infant_payload(infant: InfantProfile) -> dict[str, Any]:
    return {
        "id": str(infant.id),
        "owner_user_id": str(infant.owner_user_id),
        "infant_name": infant.infant_name,
        "sex": infant.sex,
        "birth_date": _date_iso(infant.birth_date),
        "status": infant.status,
    }


def _support_ticket_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    merged = _merge_nested_ticket(args)
    payload: dict[str, Any] = {
        "issue_type": _text(merged, "issue_type") or "other",
        "issue_summary": _text(merged, "issue_summary") or _text(merged, "summary") or _text(merged, "description"),
        "product_model": _text(merged, "product_model"),
        "order_number": _text(merged, "order_number"),
        "purchase_channel": _text(merged, "purchase_channel"),
        "user_contact": _text(merged, "user_contact"),
        "urgency": _text(merged, "urgency") or "normal",
    }
    extra_payload = merged.get("payload")
    if isinstance(extra_payload, dict):
        payload["payload"] = extra_payload
    metadata = _metadata_payload(merged)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _support_ticket_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "issue_type": _text(apply_payload, "issue_type") or "other",
        "issue_summary": _text(apply_payload, "issue_summary"),
        "product_model": _text(apply_payload, "product_model"),
        "order_number": _text(apply_payload, "order_number"),
        "purchase_channel": _text(apply_payload, "purchase_channel"),
        "urgency": _text(apply_payload, "urgency") or "normal",
        "has_user_contact": bool(_text(apply_payload, "user_contact")),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _merge_nested_ticket(args: dict[str, Any]) -> dict[str, Any]:
    merged = dict(args)
    ticket = args.get("ticket")
    if isinstance(ticket, dict):
        for key, value in ticket.items():
            if merged.get(key) in (None, ""):
                merged[key] = value
    return merged


def _metadata_payload(payload: dict[str, Any]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key in ("thread_id", "locale", "timezone"):
        value = _text(payload, key)
        if value:
            metadata[key] = value
    return metadata


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _date_iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None
