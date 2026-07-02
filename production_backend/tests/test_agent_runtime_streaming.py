from uuid import uuid4

from production_backend.app.modules.agent_runtime.models import AgentEvent
from production_backend.app.modules.agent_runtime.streaming import encode_sse_events


def test_encode_sse_events_uses_application_event_envelope() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    event = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=3,
        event_type="message.completed",
        payload={"message_id": "msg_1"},
    )

    encoded = encode_sse_events([event])

    assert "id: 3" in encoded
    assert "event: message.completed" in encoded
    assert '"thread_id":"' in encoded
    assert '"run_id":"' in encoded
    assert '"type":"message.completed"' in encoded
    assert "provider" not in encoded
