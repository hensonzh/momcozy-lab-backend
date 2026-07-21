from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
AGENT_RUNTIME_ROOT = APP_ROOT / "agent_runtime"


def test_production_backend_app_does_not_depend_on_retired_runtime_or_sqlite() -> None:
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


def test_retired_backend_layout_is_absent_from_project_root() -> None:
    retired_root_dirs = {
        "data",
        "legacy_backend",
        "skills",
        "src",
        "upload_files",
        "web_data",
    }
    leaked_dirs = sorted(name for name in retired_root_dirs if (ROOT / name).exists())

    assert leaked_dirs == []


def test_production_backend_has_no_retired_runtime_bridge() -> None:
    assert not (ROOT / "legacy_bridge").exists()


def test_retired_outbox_runtime_is_absent() -> None:
    retired_paths = [
        APP_ROOT / "modules" / "audit" / "outbox.py",
        APP_ROOT / "modules" / "files" / "outbox_handlers.py",
        APP_ROOT / "workers" / "outbox.py",
        APP_ROOT / "workers" / "registry.py",
        ROOT / "scripts" / "run_outbox_worker.py",
    ]

    assert [str(path.relative_to(ROOT)) for path in retired_paths if path.exists()] == []
    assert all("outbox" not in path.read_text().lower() for path in APP_ROOT.rglob("*.py"))


def test_agent_runtime_uses_explicit_internal_subdomains() -> None:
    expected_subdomains = {
        "actions",
        "api",
        "context",
        "evals",
        "events",
        "providers",
        "runs",
        "tools",
    }
    missing_subdomains = sorted(name for name in expected_subdomains if not (AGENT_RUNTIME_ROOT / name).is_dir())
    expected_agent_subdomains = {
        "actions",
        "context",
        "prompts",
        "skills",
        "tools",
        "workflows",
    }
    cozymate_root = APP_ROOT / "agents" / "cozymate"
    missing_agent_subdomains = sorted(
        name for name in expected_agent_subdomains if not (cozymate_root / name).is_dir()
    )

    flattened_runtime_files = {
        "action_outbox.py",
        "action_policy.py",
        "controls.py",
        "evals.py",
        "events.py",
        "execution.py",
        "memory.py",
        "memory_actions.py",
        "replay.py",
        "runtime.py",
        "safety.py",
        "state_store.py",
        "streaming.py",
        "transient_stream.py",
    }
    leaked_files = sorted(name for name in flattened_runtime_files if (AGENT_RUNTIME_ROOT / name).exists())

    assert missing_subdomains == []
    assert missing_agent_subdomains == []
    assert leaked_files == []
    assert not (APP_ROOT / "modules" / "agent_runtime").exists()


def test_business_modules_do_not_own_agent_adapters_or_depend_on_agent_layers() -> None:
    forbidden_imports = ("app.agent_runtime", "app.agents", "app.workers", "..agent_runtime", "...agent_runtime", "..workers", "...workers")
    violations: list[str] = []

    for module_root in (APP_ROOT / "modules").iterdir():
        if not module_root.is_dir() or module_root.name == "__pycache__":
            continue
        if (module_root / "agent_actions.py").exists():
            violations.append(f"{module_root.name}/agent_actions.py exists")
        for path in module_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if any(token in source for token in forbidden_imports):
                violations.append(f"{path.relative_to(APP_ROOT)} imports an agent layer")

    assert violations == []


def test_worker_package_does_not_own_action_contracts() -> None:
    assert not (APP_ROOT / "workers" / "errors.py").exists()


def test_records_module_keeps_domain_rules_out_of_service_or_infrastructure() -> None:
    records_root = APP_ROOT / "modules" / "records"
    domain = records_root / "domain.py"
    service = records_root / "service.py"
    layering_doc = ROOT / "docs" / "module-layering.md"

    assert domain.exists()
    assert "from . import domain" in service.read_text()

    domain_text = domain.read_text()
    for forbidden in [
        "fastapi",
        "ApiError",
        "AuditService",
        "IdempotencyService",
        "RecordsRepository",
        "sqlalchemy",
    ]:
        assert forbidden not in domain_text

    doc_text = layering_doc.read_text()
    assert "domain.py" in doc_text
    assert "records/domain.py" in doc_text
