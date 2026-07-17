import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.device_guidance import (
    DeviceGuidanceReferenceService,
)


def test_device_guidance_reference_service_reads_versioned_step_without_internal_asset_urls() -> None:
    service = DeviceGuidanceReferenceService()

    result = service.read(model="Air1", step="guide.parts")

    assert result["device_model"] == "Air1"
    assert result["document_version"]
    assert result["guide_outline"][:3] == ["guide.overview", "guide.parts", "guide.controls"]
    assert result["current_step"]["id"] == "guide.parts"
    assert "打开外包装" in result["current_step"]["content"]
    assert "用户确认这些都在" in result["current_step"]["completion_condition"]
    assert "/skill-assets/" not in str(result)
    assert result["current_step"]["image_labels"] == ["Air1 核心部件"]


def test_device_guidance_reference_service_uses_first_unboxing_step_when_topic_has_no_step() -> None:
    result = DeviceGuidanceReferenceService().read(model="BP334", topic="unboxing")

    assert result["device_model"] == "Air1"
    assert result["current_step"]["id"] == "guide.parts"


def test_device_guidance_parts_step_treats_direct_continue_as_completion_confirmation() -> None:
    result = DeviceGuidanceReferenceService().read(model="Air1", step="guide.parts")

    completion_condition = result["current_step"]["completion_condition"]
    assert "回复‘继续’" in completion_condition
    assert "进入 `guide.controls`" in completion_condition
    assert "仍然停留在 `guide.parts`" not in completion_condition


def test_device_guidance_reference_service_returns_bounded_faq_matches() -> None:
    result = DeviceGuidanceReferenceService().read(model="Air1", topic="faq", query="充电时可以使用吸奶器吗", limit=3)

    assert 1 <= len(result["faq_matches"]) <= 3
    assert any("充电时可以使用" in item["question"] for item in result["faq_matches"])
    assert all(set(item) == {"question", "answer"} for item in result["faq_matches"])


def test_device_guidance_reference_service_returns_focused_suction_faq_without_query() -> None:
    result = DeviceGuidanceReferenceService().read(model="Air1", topic="suction", limit=3)

    assert result["faq_matches"]
    assert any("吸力" in item["question"] for item in result["faq_matches"])


def test_device_guidance_reference_service_returns_focused_troubleshooting_faq_without_query() -> None:
    result = DeviceGuidanceReferenceService().read(model="Air1", topic="troubleshooting", limit=3)

    assert result["faq_matches"]
    assert any("不工作" in item["question"] or "吸力" in item["question"] for item in result["faq_matches"])


def test_device_guidance_reference_service_rejects_unknown_model_and_step() -> None:
    service = DeviceGuidanceReferenceService()

    with pytest.raises(ApiError) as model_error:
        service.read(model="M9", step="guide.parts")
    with pytest.raises(ApiError) as step_error:
        service.read(model="Air1", step="guide.unknown")

    assert model_error.value.code == "unsupported_device_model"
    assert step_error.value.code == "device_guidance_step_not_found"
