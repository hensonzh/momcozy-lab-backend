import pytest

from app.api.dependencies import normalize_idempotency_key
from app.core.errors import ApiError


def test_normalize_idempotency_key_trims_blank_to_none() -> None:
    assert normalize_idempotency_key(None) is None
    assert normalize_idempotency_key("   ") is None
    assert normalize_idempotency_key(" idem-1 ") == "idem-1"


def test_normalize_idempotency_key_rejects_overlong_header() -> None:
    with pytest.raises(ApiError, match="too long"):
        normalize_idempotency_key("x" * 256)
