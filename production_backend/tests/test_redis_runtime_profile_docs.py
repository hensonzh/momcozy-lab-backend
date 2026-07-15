from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "redis-runtime-profile.md"


def test_redis_runtime_profile_documents_transient_state_boundary() -> None:
    text = DOC.read_text()

    for phrase in [
        "redis-runtime-controls",
        "check_redis_runtime_controls.py",
        "agent:run:{run_id}:lock",
        "agent:thread:{thread_id}:active_run",
        "must not be the authority",
        "does not require local infrastructure",
    ]:
        assert phrase in text
