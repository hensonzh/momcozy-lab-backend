import asyncio
from uuid import uuid4

from app.modules.agent_runtime.repository import AgentRuntimeRepository


def test_recent_client_event_query_is_scoped_to_thread_owner() -> None:
    session = CapturingSession()
    owner_user_id = uuid4()

    events = asyncio.run(
        AgentRuntimeRepository(session).list_client_events_for_thread(  # type: ignore[arg-type]
            thread_id=uuid4(),
            owner_user_id=owner_user_id,
            limit=10,
        )
    )

    statement = session.statement
    compiled = statement.compile()
    sql = str(statement)
    assert events == []
    assert "JOIN agent_threads" in sql
    assert "agent_threads.owner_user_id" in sql
    assert owner_user_id in compiled.params.values()


class CapturingSession:
    def __init__(self) -> None:
        self.statement = None

    async def scalars(self, statement):
        self.statement = statement
        return EmptyScalarResult()


class EmptyScalarResult:
    @staticmethod
    def all():
        return []
