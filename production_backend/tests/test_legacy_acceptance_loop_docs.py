from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_legacy_acceptance_loop_names_inventory_and_codex_gates() -> None:
    text = (DOCS / "legacy-backend-acceptance-loop.md").read_text()

    for phrase in [
        "backend-refactor-inventory.generated.md",
        "inventory legacy behavior",
        "write tests and evals first",
        "Freeze The Experience Main Flow",
        "Experience-flow acceptance",
        "Self-Review Before Commit",
        "Definition Of Done For A Migrated Domain",
    ]:
        assert phrase in text


def test_legacy_acceptance_loop_starts_from_user_experience_flows() -> None:
    text = (DOCS / "legacy-backend-acceptance-loop.md").read_text()

    for phrase in [
        "Start from the user's main path through the feature",
        "visible success state",
        "empty/offline/error states",
        "persisted postcondition check",
        "not accepted just because individual endpoints return 200",
    ]:
        assert phrase in text


def test_legacy_acceptance_loop_covers_backend_domains_and_agent_evals() -> None:
    text = (DOCS / "legacy-backend-acceptance-loop.md").read_text()

    for phrase in [
        "Records: Feeding, Pumping, Growth",
        "Pregnancy Diary And Health Notes",
        "Pump Device, Telemetry, Workstate, And Pump Process",
        "Agent Conversation, Skill Runtime, Tools, And Streaming",
        "Product-Level Agent Acceptance Suites",
        "prompt_injection",
    ]:
        assert phrase in text
