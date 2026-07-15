from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "milk_management.db"
KNOWLEDGE_ROOT = PROJECT_ROOT / "data" / "knowledge" / "milk_management"


def get_db_path() -> Path:
    """Return the active milk-management SQLite path.

    `MILK_DB_PATH` is supported for tests or future deployment environments.
    """

    configured = os.environ.get("MILK_DB_PATH", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DB_PATH


def get_knowledge_root() -> Path:
    """Return the active milk-management knowledge/reference data directory."""

    configured = os.environ.get("MILK_KNOWLEDGE_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else KNOWLEDGE_ROOT


def connect(db_path: str | Path | None = None, *, create: bool = True) -> sqlite3.Connection:
    path = Path(db_path or get_db_path())
    if not create and not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    if db_path is None:
        _ensure_default_db_schema()
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows]


def fetch_one(sql: str, params: Iterable[Any] = (), *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    conn: sqlite3.Connection | None = None
    try:
        conn = connect(db_path, create=False)
        row = conn.execute(sql, tuple(params)).fetchone()
        return row_to_dict(row) if row is not None else None
    except FileNotFoundError:
        return None
    except sqlite3.OperationalError as exc:
        if _is_missing_table_error(exc):
            return None
        raise
    finally:
        if conn is not None:
            conn.close()


def fetch_all(sql: str, params: Iterable[Any] = (), *, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    conn: sqlite3.Connection | None = None
    try:
        conn = connect(db_path, create=False)
        rows = conn.execute(sql, tuple(params)).fetchall()
        return rows_to_dicts(rows)
    except FileNotFoundError:
        return []
    except sqlite3.OperationalError as exc:
        if _is_missing_table_error(exc):
            return []
        raise
    finally:
        if conn is not None:
            conn.close()


def execute(sql: str, params: Iterable[Any] = (), *, db_path: str | Path | None = None) -> int:
    with transaction(db_path) as conn:
        cursor = conn.execute(sql, tuple(params))
        return int(cursor.rowcount or 0)


def _ensure_default_db_schema() -> None:
    from .. import data_store

    data_store.DB_PATH = get_db_path()
    data_store.init_db()


def _is_missing_table_error(exc: sqlite3.OperationalError) -> bool:
    return "no such table" in str(exc).lower()
