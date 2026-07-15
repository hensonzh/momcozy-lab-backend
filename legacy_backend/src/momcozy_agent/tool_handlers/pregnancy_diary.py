from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..services import data_store
from ..types import RuntimeInputs


_ACTION_ALIASES = {
    "get_today": "read",
    "create": "write",
}


def manage_pregnancy_diary(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    raw_action = str(args.get("action") or "list").strip()
    action = _ACTION_ALIASES.get(raw_action, raw_action)
    user_id = _runtime_user_id(inputs)
    if not user_id:
        return _result("missing_user", action=action)

    if action == "list":
        return _list_entries(args, user_id)
    if action == "read":
        return _read_entry(args, inputs, user_id)
    if action == "write":
        return _write_entry(args, inputs, user_id)
    if action == "update":
        return _update_entry(args, inputs, user_id)
    if action == "delete":
        return _delete_entry(args, inputs, user_id)
    return _result(
        "unsupported_action",
        action=raw_action,
        side_effect_performed=False,
        summary="这个孕期日记动作已经停用，请改用 read/list/write/update/delete。",
    )


def _list_entries(args: dict[str, Any], user_id: str) -> dict[str, Any]:
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
        summary=_list_summary(entries),
    )


def _read_entry(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    entry_date = _entry_date(args, inputs)
    entry = data_store.get_pregnancy_diary_entry_by_date(user_id=user_id, entry_date=entry_date)
    return _result(
        "diary_entry_read",
        action="read",
        entry_date=entry_date,
        diary=entry,
        summary=_entry_summary(entry, entry_date=entry_date),
    )


def _write_entry(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    entry_date = _entry_date(args, inputs)
    content = _content(args)
    if not content:
        return _result("needs_diary_content", action="write", side_effect_performed=False, entry_date=entry_date)

    existing = data_store.get_pregnancy_diary_entry_by_date(user_id=user_id, entry_date=entry_date)
    if existing:
        return _result(
            "entry_already_exists",
            action="write",
            side_effect_performed=False,
            entry_date=entry_date,
            diary=existing,
            summary=f"{entry_date} 已有孕期日记，需要结合旧内容改为 update。",
        )

    diary = data_store.save_pregnancy_diary_entry(
        user_id=user_id,
        entry_date=entry_date,
        content=content,
        attachments=[],
    )
    return _result(
        "diary_entry_written" if diary else "diary_entry_write_failed",
        action="write",
        side_effect_performed=bool(diary),
        entry_date=entry_date,
        diary=diary,
        summary=_entry_summary(diary, entry_date=entry_date),
    )


def _update_entry(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    entry_date = _entry_date(args, inputs)
    existing = data_store.get_pregnancy_diary_entry_by_date(user_id=user_id, entry_date=entry_date)
    if not existing:
        return _result("entry_not_found", action="update", side_effect_performed=False, entry_date=entry_date)

    content = _content(args)
    if not content:
        return _result(
            "needs_diary_content",
            action="update",
            side_effect_performed=False,
            entry_id=existing.get("entry_id"),
            entry_date=entry_date,
        )

    diary = data_store.update_pregnancy_diary_entry(
        user_id=user_id,
        entry_id=int(existing.get("entry_id") or 0),
        entry_date=entry_date,
        gestational_week=str(existing.get("gestational_week") or ""),
        mood=str(existing.get("mood") or ""),
        energy_level=str(existing.get("energy_level") or ""),
        sleep_summary=str(existing.get("sleep_summary") or ""),
        fetal_movement=str(existing.get("fetal_movement") or ""),
        symptom_tags=list(existing.get("symptom_tags") or []),
        appointment_note=str(existing.get("appointment_note") or ""),
        nutrition_note=str(existing.get("nutrition_note") or ""),
        content=content,
        attachments=list(existing.get("attachments") or []),
    )
    return _result(
        "diary_entry_updated" if diary else "diary_entry_update_failed",
        action="update",
        side_effect_performed=bool(diary),
        entry_date=entry_date,
        diary=diary,
        summary=_entry_summary(diary, entry_date=entry_date),
    )


def _delete_entry(args: dict[str, Any], inputs: RuntimeInputs, user_id: str) -> dict[str, Any]:
    entry_date = _entry_date(args, inputs)
    existing = data_store.get_pregnancy_diary_entry_by_date(user_id=user_id, entry_date=entry_date)
    if not existing:
        return _result("entry_not_found", action="delete", side_effect_performed=False, entry_date=entry_date)
    if args.get("confirmed") is not True:
        return _result(
            "needs_delete_confirmation",
            action="delete",
            side_effect_performed=False,
            entry_id=existing.get("entry_id"),
            entry_date=entry_date,
            summary=f"删除 {entry_date} 的孕期日记前需要用户明确确认。",
        )
    deleted = data_store.delete_pregnancy_diary_entry(user_id=user_id, entry_id=int(existing.get("entry_id") or 0))
    return _result(
        "diary_entry_deleted" if deleted else "diary_entry_delete_failed",
        action="delete",
        side_effect_performed=deleted,
        entry_id=existing.get("entry_id"),
        entry_date=entry_date,
        summary=f"已删除 {entry_date} 的孕期日记。" if deleted else "删除孕期日记失败。",
    )


def _list_summary(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "暂无孕期日记。"
    latest = entries[0]
    latest_date = str(latest.get("entry_date") or "").strip()
    preview = _preview(latest.get("content"), max_chars=48)
    latest_text = f"{latest_date}，{preview}" if preview else latest_date
    return f"最近读取 {len(entries)} 条孕期日记；最新记录：{latest_text}"


def _entry_summary(entry: dict[str, Any] | None, *, entry_date: str) -> str:
    if not entry:
        return f"{entry_date} 暂无孕期日记。"
    preview = _preview(entry.get("content"), max_chars=72)
    return f"{entry_date}，{preview}" if preview else f"{entry_date}，孕期日记已保存。"


def _preview(value: Any, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


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


def _entry_date(args: dict[str, Any], inputs: RuntimeInputs) -> str:
    return _date_arg(args.get("entry_date")) or _today(inputs)


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


def _content(args: dict[str, Any]) -> str:
    return str(args.get("content") or "").strip()
