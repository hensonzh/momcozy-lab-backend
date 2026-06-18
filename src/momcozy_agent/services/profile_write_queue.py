from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from . import data_store

_LOGGER = logging.getLogger(__name__)
_MAX_RETRIES = 2


@dataclass
class _ProfileWriteTask:
    operation: str
    kwargs: dict[str, Any]
    attempts: int = 0


_QUEUE: queue.Queue[_ProfileWriteTask] = queue.Queue()
_WORKER_LOCK = threading.Lock()
_WORKER_THREAD: threading.Thread | None = None


def enqueue_user_profile_update(
    *,
    user_id: str,
    display_name: str | None = None,
    age: int | None = None,
    onboarding_skipped: bool | None = None,
) -> bool:
    uid = str(user_id or "").strip()
    if not uid:
        return False
    return _enqueue(
        "user_profile",
        {
            "user_id": uid,
            "display_name": display_name,
            "age": age,
            "onboarding_skipped": onboarding_skipped,
        },
    )


def enqueue_birth_prep_profile_update(
    *,
    user_id: str,
    age: Any = None,
    due_date_or_week: Any = None,
    ivf: Any = None,
    fetus_count: Any = None,
    city_or_country: Any = None,
    birth_hospital: Any = None,
    birth_path: Any = None,
    first_birth: Any = None,
    feeding_intention: Any = None,
    return_to_work_timing: Any = None,
    support_person: Any = None,
    pregnancy_history_or_notes: Any = None,
    top_worries: Any = None,
) -> bool:
    uid = str(user_id or "").strip()
    if not uid:
        return False
    return _enqueue(
        "birth_prep_profile",
        {
            "user_id": uid,
            "age": age,
            "due_date_or_week": due_date_or_week,
            "ivf": ivf,
            "fetus_count": fetus_count,
            "city_or_country": city_or_country,
            "birth_hospital": birth_hospital,
            "birth_path": birth_path,
            "first_birth": first_birth,
            "feeding_intention": feeding_intention,
            "return_to_work_timing": return_to_work_timing,
            "support_person": support_person,
            "pregnancy_history_or_notes": pregnancy_history_or_notes,
            "top_worries": top_worries,
        },
    )


def wait_for_pending_profile_writes(timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    with _QUEUE.all_tasks_done:
        while _QUEUE.unfinished_tasks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _QUEUE.all_tasks_done.wait(remaining)
    return True


def _enqueue(operation: str, kwargs: dict[str, Any]) -> bool:
    _ensure_worker()
    _QUEUE.put(_ProfileWriteTask(operation=operation, kwargs=_clean_kwargs(kwargs)))
    return True


def _clean_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


def _ensure_worker() -> None:
    global _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            return
        _WORKER_THREAD = threading.Thread(target=_worker_loop, daemon=True, name="momcozy-profile-writes")
        _WORKER_THREAD.start()


def _worker_loop() -> None:
    while True:
        task = _QUEUE.get()
        try:
            _execute_task_with_retries(task)
        finally:
            _QUEUE.task_done()


def _execute_task_with_retries(task: _ProfileWriteTask) -> None:
    while True:
        try:
            _execute_task(task)
            return
        except Exception:
            if task.attempts >= _MAX_RETRIES:
                _LOGGER.exception("Profile memory write failed after retries: %s", task.operation)
                return
            task.attempts += 1


def _execute_task(task: _ProfileWriteTask) -> None:
    if task.operation == "user_profile":
        data_store.update_user_profile_memory(**task.kwargs)
        return
    if task.operation == "birth_prep_profile":
        data_store.update_birth_prep_profile_memory(**task.kwargs)
        return
    raise ValueError(f"Unsupported profile write operation: {task.operation}")
