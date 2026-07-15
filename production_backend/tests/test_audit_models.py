from production_backend.app.infrastructure.db.base import Base
from production_backend.app.infrastructure.db import models as _models


def test_audit_log_table_has_actor_request_and_details() -> None:
    table = Base.metadata.tables["audit_logs"]

    assert "actor_user_id" in table.columns
    assert "actor_type" in table.columns
    assert "actor_service" in table.columns
    assert "request_id" in table.columns
    assert "details_json" in table.columns


def test_idempotency_keys_have_actor_scope_key_uniqueness() -> None:
    table = Base.metadata.tables["idempotency_keys"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "uq_idempotency_actor_scope_key" in constraint_names


def test_audit_indexes_support_replay_and_resource_lookup() -> None:
    audit_indexes = {index.name for index in Base.metadata.tables["audit_logs"].indexes}
    idempotency_indexes = {index.name for index in Base.metadata.tables["idempotency_keys"].indexes}

    assert "ix_audit_logs_request_id" in audit_indexes
    assert "ix_audit_logs_actor_service" in audit_indexes
    assert "ix_audit_logs_resource" in audit_indexes
    assert "ix_idempotency_keys_expires_at" in idempotency_indexes


def test_outbox_jobs_have_worker_indexes_and_idempotency() -> None:
    table = Base.metadata.tables["outbox_jobs"]
    index_names = {index.name for index in table.indexes}
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "uq_outbox_jobs_idempotency_key" in constraint_names
    assert "ix_outbox_jobs_status_next_attempt" in index_names
    assert "ix_outbox_jobs_locked_until" in index_names
    assert "payload_json" in table.columns
    assert table.columns["status"].nullable is False
