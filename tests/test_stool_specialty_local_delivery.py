"""Local delivery starts the complete stool-analysis chain."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_analysis_worker_waits_for_specialty_service() -> None:
    compose = (ROOT / "docker-compose.local.yml").read_text()
    worker = compose.split("  stool-analysis-worker:", 1)[1].split("\n  stool-specialty:", 1)[0]
    assert "python -m app.workers.stool_analyses" in worker
    assert "stool-specialty:" in worker
    assert "condition: service_healthy" in worker.split("stool-specialty:", 1)[1]


def test_standard_local_make_targets_include_analysis_chain() -> None:
    makefile = (ROOT / "Makefile").read_text()
    build = makefile.split("backend-build:\n", 1)[1].split("\n\n", 1)[0]
    up = makefile.split("backend-up:\n", 1)[1].split("\n\n", 1)[0]
    for service in ("stool-analysis-worker", "stool-specialty"):
        assert service in build
        assert service in up
