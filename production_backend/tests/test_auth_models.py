from production_backend.app.infrastructure.db.base import Base
from production_backend.app.infrastructure.db import models as _models


def test_device_sessions_are_user_scoped() -> None:
    table = Base.metadata.tables["device_sessions"]

    assert "user_id" in table.columns
    assert table.columns["user_id"].nullable is False


def test_refresh_tokens_are_unique_by_token_hash() -> None:
    table = Base.metadata.tables["refresh_tokens"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "uq_refresh_tokens_token_hash" in constraint_names


def test_refresh_token_indexes_support_rotation_and_expiry() -> None:
    table = Base.metadata.tables["refresh_tokens"]
    index_names = {index.name for index in table.indexes}

    assert "ix_refresh_tokens_family_status" in index_names
    assert "ix_refresh_tokens_session_status" in index_names
    assert "ix_refresh_tokens_expires_at" in index_names
