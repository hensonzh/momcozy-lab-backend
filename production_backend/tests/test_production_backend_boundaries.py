from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "production_backend" / "app"


def test_production_backend_app_does_not_depend_on_legacy_runtime_or_sqlite() -> None:
    banned_tokens = {
        "previous_response_id",
        "src.momcozy_agent",
        "momcozy_agent.services.data_store",
        "from ..services import data_store",
        "import sqlite3",
    }

    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        text = path.read_text()
        for token in banned_tokens:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {token}")

    assert offenders == []


def test_legacy_backend_is_isolated_from_project_root() -> None:
    assert (ROOT / "legacy_backend" / "src" / "momcozy_agent").is_dir()

    legacy_root_dirs = {
        "data",
        "scripts",
        "skills",
        "src",
        "tests",
        "upload_files",
        "web_data",
    }
    leaked_dirs = sorted(name for name in legacy_root_dirs if (ROOT / name).exists())

    assert leaked_dirs == []
