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


def test_client_context_preserves_bounded_workflow_command_without_projecting_it_to_the_model() -> None:
    raw_context = {
        "locale": "zh-CN",
        "workflow_command": {
            "schema_version": "pregnancy_plan_command.v1",
            "workflow_type": "pregnancy_plan",
            "command": "answer_current",
            "step_id": "checkup_done",
            "choice_id": "confirm_no_checkup_yet",
            "answer": "  用户补充  ",
            "unexpected": "drop-me",
        },
    }

    sanitized = sanitize_cozymate_client_context(raw_context)

    assert sanitized["workflow_command"] == {
        "schema_version": "pregnancy_plan_command.v1",
        "workflow_type": "pregnancy_plan",
        "command": "answer_current",
        "step_id": "checkup_done",
        "choice_id": "confirm_no_checkup_yet",
        "answer": "用户补充",
    }
    assert project_cozymate_client_context(raw_context) == {"locale": "zh-CN"}


def test_client_context_drops_unknown_or_versionless_workflow_commands() -> None:
    assert sanitize_cozymate_client_context(
        {
            "workflow_command": {
                "workflow_type": "pregnancy_plan",
                "command": "answer_current",
            }
        }
    ) == {}
    assert sanitize_cozymate_client_context(
        {
            "workflow_command": {
                "schema_version": "pregnancy_plan_command.v1",
                "workflow_type": "pregnancy_plan",
                "command": "delete_everything",
            }
        }
    ) == {}
