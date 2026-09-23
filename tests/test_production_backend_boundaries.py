from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"


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


def test_embedded_agent_runtime_is_absent_and_internal_product_adapters_remain() -> None:
    for retired_path in (
        APP_ROOT / "agent_runtime",
        APP_ROOT / "agents",
        APP_ROOT / "modules" / "agent_runtime",
    ):
        assert not retired_path.exists() or not any(retired_path.rglob("*.py"))

    for module in ("files",):
        module_root = APP_ROOT / "modules" / module
        for filename in ("agent_contracts.py", "agent_router.py", "agent_service.py"):
            assert (module_root / filename).is_file()


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


def test_product_workers_dispatch_business_jobs_without_embedding_a_model_runtime() -> None:
    workers = APP_ROOT / "workers"
    for path in workers.rglob('*.py'):
        source = path.read_text()
        assert all(token not in source for token in (
            'from openai', 'import openai', 'from agents', 'from langgraph',
            'app.agent_runtime', 'app.agents',
        )), str(path.relative_to(APP_ROOT))


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


def test_retired_product_and_agent_adapters_are_removed() -> None:
    for domain in ("diary", "mother"):
        assert not (APP_ROOT / "modules" / domain).exists()
    for domain in ("plans", "records", "profiles"):
        assert not (APP_ROOT / "modules" / domain / "agent_service.py").exists()
    assert (APP_ROOT / "modules" / "profiles" / "agent_router.py").is_file()
