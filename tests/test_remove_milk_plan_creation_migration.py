from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "versions" / "20260727_0053_remove_milk_plan_creation.py"


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("remove_milk_plan_creation_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_terminates_unfinished_removed_actions_and_active_runs() -> None:
    migration = _migration_module()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert migration.down_revision == "20260727_0052"
    assert "plans.milk_plan.create" in sql
    assert "milk_plan_creation_removed" in sql
    assert "'proposed', 'confirmation_required', 'confirmed', 'applying'" in sql
    assert "'queued', 'running', 'waiting_for_confirmation'" in sql
    assert "SET status = 'failed'" in sql
