from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_productization_roadmap_defines_phase_zero_to_six_loop() -> None:
    text = (DOCS / "productization-roadmap.md").read_text()

    for phrase in [
        "Phase 0 -> Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 -> Phase 5 -> Phase 6",
        "Flutter integration is intentionally excluded",
        "Phase 0: Productization Baseline And Acceptance Matrix",
        "Phase 1: Backend Infrastructure And Environment Hardening",
        "Phase 2: Core Product Business Modules",
        "Phase 3: Worker, Outbox, And Async Effect Lane",
        "Phase 4: Production Agent Runtime",
        "Phase 5: Safety, Privacy, Permission, And Cost Controls",
        "Phase 6: Observability, Test Gates, And Release Readiness",
    ]:
        assert phrase in text


def test_productization_roadmap_keeps_flutter_as_handoff_not_blocker() -> None:
    text = (DOCS / "productization-roadmap.md").read_text()

    for phrase in [
        "Out of scope before Flutter integration",
        "must not add temporary compatibility code for the legacy app",
        "no Phase 0-6 item is blocked by Flutter implementation details",
        "Stop before Flutter integration",
        "api-contract-handoff.md",
        "openapi.generated.json",
        "flutter-smoke-flows.json",
    ]:
        assert phrase in text


def test_productization_roadmap_has_backend_completion_gates() -> None:
    text = (DOCS / "productization-roadmap.md").read_text()

    for phrase in [
        "python -m ruff check app tests scripts",
        "python -m mypy app scripts",
        "python -m pytest production_backend/tests",
        "python production_backend/scripts/run_agent_seed_eval.py",
        "OpenAPI export has no drift",
        "all backend-local PR slices",
    ]:
        assert phrase in text


def test_docs_readme_links_productization_roadmap() -> None:
    text = (DOCS / "README.md").read_text()

    assert "productization-roadmap.md" in text
    assert "Phase 0-6 backend-only productization roadmap" in text
