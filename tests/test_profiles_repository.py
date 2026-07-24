import asyncio
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.profiles.repository import ProfileRepository


def test_lactation_context_projection_does_not_select_unneeded_profile_fields() -> None:
    session = CapturingSession()
    repository = ProfileRepository(session)

    asyncio.run(repository.get_lactation_mother_context(owner_user_id=uuid4()))
    mother_sql = _compile_sql(session.statement)

    asyncio.run(repository.list_current_delivery_infant_contexts(owner_user_id=uuid4()))
    infant_sql = _compile_sql(session.statement)

    assert "user_profiles.age" in mother_sql
    assert "user_profiles.preferred_name" in mother_sql
    assert "user_profiles.estimated_due_date" in mother_sql
    assert "infant_profiles.sex_at_birth" in infant_sql
    assert "infant_profiles.name" in infant_sql


class CapturingSession:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return EmptyResult()


class EmptyResult:
    def one_or_none(self):
        return None

    def all(self):
        return []


def _compile_sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))
