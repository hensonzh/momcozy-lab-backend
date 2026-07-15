from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = PROJECT_ROOT / "data"
HISTORY_DB_ROOT = DATA_ROOT / "history_db"
UPLOAD_ROOT = Path(os.getenv("MILK_UPLOAD_DIR", str(PROJECT_ROOT / "upload_files")))
LOG_ROOT = PROJECT_ROOT / "logs"
MILK_PROCESS_CONFIG_ROOT = Path(os.getenv("MILK_PROCESS_CONFIG_DIR", str(DATA_ROOT / "milk_process" / "configs")))
MILK_PROCESS_LOG_ROOT = Path(os.getenv("MILK_PROCESS_LOG_DIR", str(LOG_ROOT / "milk_process")))


def ensure_runtime_dirs() -> None:
    for path in (DATA_ROOT, HISTORY_DB_ROOT, UPLOAD_ROOT, LOG_ROOT, MILK_PROCESS_CONFIG_ROOT, MILK_PROCESS_LOG_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def db_path(env_name: str, filename: str) -> Path:
    ensure_runtime_dirs()
    configured = os.getenv(env_name, "").strip()
    return Path(configured) if configured else HISTORY_DB_ROOT / filename

