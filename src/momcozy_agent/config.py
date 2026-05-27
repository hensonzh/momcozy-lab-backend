from __future__ import annotations

import os
from pathlib import Path


def load_project_env(path: str | Path | None = None, *, override: bool = False) -> Path | None:
    env_path = Path(path) if path is not None else _find_env_file(Path.cwd())
    if env_path is None or not env_path.exists():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_quotes(value.strip())
        if key and (override or key not in os.environ):
            os.environ[key] = value

    return env_path


def get_openai_api_key() -> str:
    load_project_env()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to .env or your shell environment.")
    return api_key


def get_openai_client_options() -> dict[str, float | int]:
    """Shared OpenAI SDK options for agent-side model calls."""
    return {
        "timeout": _float_env("MOMCOZY_OPENAI_TIMEOUT_SECONDS", 90.0),
        "max_retries": _int_env("MOMCOZY_OPENAI_MAX_RETRIES", 2),
    }


def _find_env_file(start: Path) -> Path | None:
    current = start.resolve()
    for directory in [current, *current.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _float_env(key: str, default: float) -> float:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _int_env(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default
