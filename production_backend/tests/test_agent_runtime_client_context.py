from datetime import datetime, timezone
from uuid import uuid4

from production_backend.app.modules.agent_runtime.client_context import sanitize_agent_client_context
from production_backend.app.modules.agent_runtime.models import AgentMessage
from production_backend.app.modules.agent_runtime.run_lifecycle.executor import _user_context


def test_client_context_preserves_an_explicit_empty_cart_and_sanitizes_unknown_fields() -> None:
    context = sanitize_agent_client_context(
        {
            "source": "flutter-agent-hub",
            "locale": "zh-CN",
            "unknown": "drop-me",
            "hospital_bag_cart": {
                "groups": [],
                "totals": {"item_count": 0, "total": 0, "unknown": "drop-me"},
                "unknown": "drop-me",
            },
        }
    )

    assert context == {
        "source": "flutter-agent-hub",
        "locale": "zh-CN",
        "hospital_bag_cart": {"groups": [], "totals": {"itemCount": 0, "total": 0.0}},
    }


def test_user_context_projects_the_current_cart_without_transport_metadata() -> None:
    message = AgentMessage(
        id=uuid4(),
        thread_id=uuid4(),
        run_id=uuid4(),
        role="user",
        message_type="text",
        content={
            "text": "删掉吸奶器",
            "client_context": {
                "source": "flutter-agent-hub",
                "locale": "zh-CN",
                "hospital_bag_cart": {
                    "groups": [
                        {
                            "title": "母乳喂养",
                            "tone": "sky",
                            "items": [{"id": "pump-custom", "name": "个性化吸奶器", "qty": 1, "price": 999.0}],
                        }
                    ],
                    "totals": {"itemCount": 1, "total": 919.08},
                },
            },
        },
        status="completed",
        sequence=1,
    )

    context = _user_context(current_message=message, now=datetime(2026, 7, 11, tzinfo=timezone.utc))

    assert context["locale"] == "zh-CN"
    assert context["hospital_bag_cart"]["groups"][0]["items"][0]["id"] == "pump-custom"
    assert "source" not in context
