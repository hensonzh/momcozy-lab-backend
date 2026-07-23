import pytest

from app.core.errors import ApiError
from app.agents.cozymate.device_guidance import (
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


def test_device_guidance_reference_service_rejects_unknown_model_and_step() -> None:
    service = DeviceGuidanceReferenceService()

    with pytest.raises(ApiError) as model_error:
        service.read(model="M9", step="guide.parts")
    with pytest.raises(ApiError) as topic_error:
        service.read(model="Air1", topic="unsupported")
    with pytest.raises(ApiError) as step_error:
        service.read(model="Air1", step="guide.unknown")

    assert model_error.value.code == "unsupported_device_model"
    assert topic_error.value.code == "device_guidance_topic_not_found"
    assert step_error.value.code == "device_guidance_step_not_found"
