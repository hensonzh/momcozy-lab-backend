import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.records.repository import RecordsRepository


def test_records_repository_uses_exclusive_end_boundary_for_time_windows() -> None:
    owner_user_id = uuid4()
    start_at = datetime(2026, 7, 3, tzinfo=timezone.utc)
    end_at = datetime(2026, 7, 4, tzinfo=timezone.utc)
    session = CapturingSession()
    repository = RecordsRepository(session)

    asyncio.run(
        repository.list_feedings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=10,
        )
    )
    feeding_sql = _compile_sql(session.statement)

    asyncio.run(
        repository.list_pumpings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=10,
        )
    )
    pumping_sql = _compile_sql(session.statement)

    assert "feeding_records.feed_time < " in feeding_sql
    assert "feeding_records.feed_time <= " not in feeding_sql
    assert "pumping_records.pump_start_time < " in pumping_sql
    assert "pumping_records.pump_start_time <= " not in pumping_sql


def test_records_repository_batches_latest_growth_for_multiple_infants() -> None:
    owner_user_id = uuid4()
    infant_ids = [uuid4(), uuid4()]
    session = CapturingExecuteSession()
    repository = RecordsRepository(session)

    result = asyncio.run(
        repository.list_latest_growth_by_infant_ids(
            owner_user_id=owner_user_id,
            infant_ids=infant_ids,
        )
    )
    sql = _compile_sql(session.statement)

    assert result == {}
    assert "row_number() OVER" in sql
    assert "PARTITION BY growth_records.infant_id" in sql
    assert "infant_profiles.owner_user_id" in sql
    assert "growth_records.name" not in sql


class CapturingSession:
    def __init__(self) -> None:
        self.statement = None

    async def scalars(self, statement):
        self.statement = statement
        return EmptyScalarResult()


class EmptyScalarResult:
    def all(self):
        return []


class CapturingExecuteSession:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return EmptyRowResult()


class EmptyRowResult:
    def all(self):
        return []


def _compile_sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))
