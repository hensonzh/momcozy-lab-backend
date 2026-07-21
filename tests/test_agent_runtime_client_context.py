from datetime import datetime, timezone
from uuid import uuid4

from app.agents.cozymate.context.client import (
    project_cozymate_client_context,
    sanitize_cozymate_client_context,
)
from app.agent_runtime.runs.models import AgentMessage
from app.agents.cozymate.executor import _user_context


def test_client_context_preserves_an_explicit_empty_cart_and_sanitizes_unknown_fields() -> None:
    context = sanitize_cozymate_client_context(
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


def test_client_context_preserves_workflow_reply_without_projecting_it_to_the_model() -> None:
    workflow_state_id = uuid4()
    raw_context = {
        "locale": "zh-CN",
        "workflow_reply": {
            "workflow_state_id": str(workflow_state_id),
            "workflow_type": "pregnancy_plan",
            "revision": 7,
            "step_token": "opaque-step-token",
            "unexpected": "drop-me",
        },
    }

    sanitized = sanitize_cozymate_client_context(raw_context)

    assert sanitized["workflow_reply"] == {
        "workflow_state_id": str(workflow_state_id),
        "workflow_type": "pregnancy_plan",
        "revision": 7,
        "step_token": "opaque-step-token",
    }
    assert project_cozymate_client_context(raw_context) == {"locale": "zh-CN"}
