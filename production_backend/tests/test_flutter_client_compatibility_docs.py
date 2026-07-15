from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "flutter-client-compatibility.md"


def test_flutter_client_compatibility_doc_names_contract_sources() -> None:
    text = DOC.read_text()

    for phrase in [
        "openapi.generated.json",
        "api-contract-handoff.md",
        "flutter-smoke-flows.json",
        "OpenAPI snapshot",
    ]:
        assert phrase in text


def test_flutter_client_compatibility_doc_protects_token_and_agent_contracts() -> None:
    text = DOC.read_text()

    for phrase in [
        "Never put access tokens",
        "POST /v1/auth/refresh",
        "Idempotency-Key",
        "after_sequence",
        "action_id",
    ]:
        assert phrase in text
