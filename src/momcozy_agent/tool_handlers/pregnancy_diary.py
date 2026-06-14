from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..services import data_store
from ..types import RuntimeInputs


PREGNANCY_DIARY_FIELDS = (
    "gestational_week",
    "mood",
    "energy_level",
    "sleep_summary",
    "fetal_movement",
    "appointment_note",
    "nutrition_note",
    "content",
)


def manage_pregnancy_diary(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    action = str(args.get("action") or "list").strip()
    user_id = _runtime_user_id(inputs)
    if not user_id:
        return _result("missing_user", action=action)

    if action == "list":
        return _list_entries(args, inputs, user_id)
    if action == "get_today":
        return _get_today(args, inputs, user_id)
    if action == "create":
        return _create_entry(args, inputs, user_id)
    if action == "update":
        return _update_entry(args, inputs, user_id)
    if action == "delete":
        return _delete_entry(args, inputs, user_id)
    if action == "record_health_consultation":
        return _record_health_consultation(args, inputs, user_id)
    return _result("unsupported_action", action=action)


def _list_entries(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    limit = _safe_limit(args.get("limit"), default=7)
    entries = data_store.list_pregnancy_diary_entries(
        user_id=user_id,
        start_date=_date_arg(args.get("start_date")),
        end_date=_date_arg(args.get("end_date")),
        limit=limit,
    )
    return _result(
        "diary_list_read",
        action="list",
        diary_list=entries,
        summary=_summary(entries),
    )


def _get_today(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    entry_date = _date_arg(args.get("entry_date")) or _today(inputs)
    entry = data_store.get_pregnancy_diary_entry_by_date(user_id=user_id, entry_date=entry_date)
    return _result("diary_entry_read", action="get_today", diary=entry, summary=_entry_summary(entry))


def _create_entry(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    entry_date = _date_arg(args.get("entry_date")) or _today(inputs)
    if data_store.get_pregnancy_diary_entry_by_date(user_id=user_id, entry_date=entry_date):
        return _result("entry_already_exists", action="create", side_effect_performed=False, entry_date=entry_date)
    if not _has_diary_content(args):
        return _result("needs_diary_content", action="create", side_effect_performed=False, entry_date=entry_date)
    diary = data_store.save_pregnancy_diary_entry(
        user_id=user_id,
        entry_date=entry_date,
        gestational_week=_text(args.get("gestational_week")),
        mood=_text(args.get("mood")),
        energy_level=_text(args.get("energy_level")),
        sleep_summary=_text(args.get("sleep_summary")),
        fetal_movement=_text(args.get("fetal_movement")),
        symptom_tags=_string_list(args.get("symptom_tags")),
        appointment_note=_text(args.get("appointment_note")),
        nutrition_note=_text(args.get("nutrition_note")),
        content=_text(args.get("content")),
        attachments=[],
    )
    return _result(
        "diary_entry_created" if diary else "diary_entry_create_failed",
        action="create",
        side_effect_performed=bool(diary),
        diary=diary,
        summary=_entry_summary(diary),
    )


def _update_entry(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    existing = _target_entry(args, inputs, user_id)
    if not existing:
        return _result("entry_not_found", action="update", side_effect_performed=False)
    if not _has_diary_content(args):
        return _result(
            "needs_diary_content",
            action="update",
            side_effect_performed=False,
            entry_id=existing.get("entry_id"),
            entry_date=existing.get("entry_date"),
        )

    entry_date = _date_arg(args.get("entry_date")) or str(existing.get("entry_date") or "") or _today(inputs)
    merged = {field: _merge_text(args, existing, field) for field in PREGNANCY_DIARY_FIELDS}
    tags = _string_list(args.get("symptom_tags")) if args.get("symptom_tags") is not None else list(existing.get("symptom_tags") or [])
    diary = data_store.update_pregnancy_diary_entry(
        user_id=user_id,
        entry_id=int(existing.get("entry_id") or 0),
        entry_date=entry_date,
        symptom_tags=tags,
        attachments=list(existing.get("attachments") or []),
        **merged,
    )
    return _result(
        "diary_entry_updated" if diary else "diary_entry_update_failed",
        action="update",
        side_effect_performed=bool(diary),
        diary=diary,
        summary=_entry_summary(diary),
    )


def _delete_entry(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    existing = _target_entry(args, inputs, user_id)
    if not existing:
        return _result("entry_not_found", action="delete", side_effect_performed=False)
    if args.get("confirmed") is not True:
        return _result(
            "needs_delete_confirmation",
            action="delete",
            side_effect_performed=False,
            entry_id=existing.get("entry_id"),
            entry_date=existing.get("entry_date"),
            summary="删除孕期日记前需要用户明确确认。",
        )
    deleted = data_store.delete_pregnancy_diary_entry(user_id=user_id, entry_id=int(existing.get("entry_id") or 0))
    return _result(
        "diary_entry_deleted" if deleted else "diary_entry_delete_failed",
        action="delete",
        side_effect_performed=deleted,
        entry_id=existing.get("entry_id"),
        entry_date=existing.get("entry_date"),
        summary="已删除这条孕期日记。" if deleted else "删除孕期日记失败。",
    )


def _record_health_consultation(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    entry_date = _date_arg(args.get("entry_date")) or _today(inputs)
    if not _has_health_note_content(args):
        return _result(
            "needs_health_consultation_content",
            action="record_health_consultation",
            side_effect_performed=False,
            entry_date=entry_date,
        )
    note = data_store.add_pregnancy_diary_health_note(
        user_id=user_id,
        entry_date=entry_date,
        topic=_text(args.get("health_topic")),
        user_report=_text(args.get("health_user_report")),
        asked_questions=_string_list(args.get("health_asked_questions")),
        known_answers=_string_list(args.get("health_known_answers")),
        suggestion_summary=_text(args.get("health_suggestion_summary")),
        follow_up=_text(args.get("health_follow_up")),
    )
    diary = data_store.get_pregnancy_diary_entry_by_date(user_id=user_id, entry_date=entry_date)
    status = "health_consultation_record_failed"
    if note:
        status = "health_consultation_updated" if note.get("mutation") == "updated" else "health_consultation_recorded"
    return _result(
        status,
        action="record_health_consultation",
        side_effect_performed=bool(note),
        entry_date=entry_date,
        health_note=note,
        diary=diary,
        summary=_health_note_summary(note),
    )


def _target_entry(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any] | None:
    try:
        entry_id = int(args.get("entry_id") or 0)
    except Exception:
        entry_id = 0
    if entry_id > 0:
        return data_store.get_pregnancy_diary_entry(user_id=user_id, entry_id=entry_id)
    entry_date = _date_arg(args.get("entry_date")) or _today(inputs)
    return data_store.get_pregnancy_diary_entry_by_date(user_id=user_id, entry_date=entry_date)


def _has_diary_content(args: dict[str, Any]) -> bool:
    if any(args.get(field) is not None for field in PREGNANCY_DIARY_FIELDS):
        return True
    return args.get("symptom_tags") is not None


def _has_health_note_content(args: dict[str, Any]) -> bool:
    return any(
        _text(args.get(field))
        for field in ("health_topic", "health_user_report", "health_suggestion_summary", "health_follow_up")
    ) or bool(_string_list(args.get("health_asked_questions"))) or bool(_string_list(args.get("health_known_answers")))


def _merge_text(args: dict[str, Any], existing: dict[str, Any], field: str) -> str:
    if args.get(field) is None:
        return str(existing.get(field) or "")
    return _text(args.get(field))


def _summary(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "暂无孕期日记。"
    questions = sum(1 for entry in entries if str(entry.get("appointment_note") or "").strip())
    latest = _entry_summary(entries[0])
    return f"最近读取 {len(entries)} 条孕期日记；其中 {questions} 条包含产检问题；最新记录：{latest}"


def _entry_summary(entry: dict[str, Any] | None) -> str:
    if not entry:
        return "未找到孕期日记。"
    parts = [
        str(entry.get("entry_date") or "").strip(),
        str(entry.get("gestational_week") or "").strip(),
        str(entry.get("mood") or "").strip(),
        str(entry.get("fetal_movement") or "").strip(),
    ]
    return "，".join(part for part in parts if part) or "孕期日记已保存。"


def _health_note_summary(note: dict[str, Any] | None) -> str:
    if not note:
        return "健康咨询记录保存失败。"
    parts = [
        str(note.get("entry_date") or "").strip(),
        str(note.get("topic") or "").strip(),
        str(note.get("user_report") or "").strip(),
    ]
    return "，".join(part for part in parts if part) or "健康咨询已记录到孕期日记。"


def _result(status: str, *, action: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tool_name": "pregnancy_diary_manage",
        "status": status,
        "action": action,
    }
    result.update(extra)
    return result


def _runtime_user_id(inputs: RuntimeInputs) -> str:
    profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    return str(inputs.get("user_id") or profile.get("user_id") or "").strip()


def _today(inputs: RuntimeInputs) -> str:
    raw = str(inputs.get("message_sent_at") or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            pass
    return date.today().isoformat()


def _date_arg(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return text if len(text) == 10 and text[4] == "-" and text[7] == "-" else ""


def _safe_limit(raw: Any, *, default: int) -> int:
    try:
        value = int(raw or default)
    except Exception:
        value = default
    return min(max(value, 1), 30)


def _text(raw: Any) -> str:
    return str(raw or "").strip()


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]
