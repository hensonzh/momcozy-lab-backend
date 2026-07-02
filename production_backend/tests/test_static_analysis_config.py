from pathlib import Path


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_mypy_config_uses_progressively_tighter_backend_gates() -> None:
    text = PYPROJECT.read_text()

    for phrase in [
        "check_untyped_defs = true",
        "disallow_any_generics = true",
        "disallow_incomplete_defs = true",
        "disallow_untyped_calls = true",
        "no_implicit_optional = true",
        "strict_equality = true",
        "warn_redundant_casts = true",
        "warn_return_any = true",
        "warn_unused_ignores = true",
    ]:
        assert phrase in text
