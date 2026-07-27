import pytest

from app.agent_runtime.tools.validation import validate_tool_input
from app.agents.cozymate.tools import default_tool_registry
from app.core.errors import ApiError


def test_profile_read_replaces_raw_growth_history_as_direct_tool() -> None:
    registry = default_tool_registry()
    contract = registry.get("profile_read")
    names = set(registry.names_for_sdk())

    assert contract.domain == "profiles"
    assert contract.effect_scope == "none"
    assert contract.input_schema["type"] == "object"
    assert contract.input_schema["additionalProperties"] is False
    infant_scope = contract.input_schema["properties"]["infant_scope"]
    assert infant_scope["enum"] == ["current_delivery", "all"]
    assert infant_scope["default"] == "current_delivery"
    assert infant_scope["description"]
    assert "妈妈资料和宝宝资料" in contract.description
    assert "不包含奶量产出和摄入记录" in contract.description
    assert "profile_read" in names
    assert "profile_update" in names
    assert "maternal_infant_profile_read" not in names
    assert "maternal_infant_profile_update" not in names
    assert "lactation_context_read" not in names
    assert "records_growth_read" not in names


def test_profile_read_declares_described_nested_output_schema() -> None:
    contract = default_tool_registry().get("profile_read")

    assert contract.output_schema is not None
    assert contract.output_schema["additionalProperties"] is False
    assert set(contract.output_schema["required"]) == {
        "as_of_date",
        "infant_scope",
        "mother",
        "infants",
        "missing_fields",
        "data_quality_issues",
    }
    assert "可信运行时" in contract.output_schema["properties"]["as_of_date"]["description"]

    definitions = contract.output_schema["$defs"]
    mother = definitions["LactationMotherContextOutput"]
    infant = definitions["LactationInfantContextOutput"]
    measurement = definitions["LatestInfantMeasurementOutput"]
    missing_field = definitions["LactationMissingFieldOutput"]
    quality_issue = definitions["LactationDataQualityIssueOutput"]

    assert "实际分娩日期" in mother["properties"]["estimated_due_date"]["description"]
    assert "累计分娩次数" in mother["properties"]["delivery_count"]["description"]
    assert "分娩当天为 0" in mother["properties"]["postpartum_days"]["description"]
    assert "稳定 UUID" in infant["properties"]["infant_id"]["description"]
    assert "当前这次分娩" in infant["properties"]["is_current_delivery"]["description"]
    assert infant["properties"]["sex_at_birth"]["anyOf"][0]["enum"] == [
        "female",
        "male",
        "intersex",
        "unknown",
        "undisclosed",
    ]
    assert "完整日历月" in infant["properties"]["age_months"]["description"]
    assert "kg" in measurement["properties"]["weight_kg"]["description"]
    assert "cm" in measurement["properties"]["height_cm"]["description"]
    assert missing_field["properties"]["code"]["enum"]
    assert quality_issue["properties"]["code"]["enum"]
    assert "birth_order" in missing_field["required"]
    assert "birth_order" in quality_issue["required"]

    object_schemas = [contract.output_schema, *definitions.values()]
    for object_schema in object_schemas:
        for field_name, field_schema in object_schema.get("properties", {}).items():
            assert field_schema.get("description"), f"{object_schema['title']}.{field_name} lacks a description"


def test_profile_update_is_the_described_profile_update_superset() -> None:
    contract = default_tool_registry().get("profile_update")

    assert contract.domain == "profiles"
    assert contract.effect_scope == "user_resource"
    assert contract.action_types == (
        "profile.update",
        "profile.current_infants.replace",
    )
    assert "action_type" not in type(contract).model_fields
    assert contract.description.startswith("更新妈妈的称呼、年龄、孕产和喂养基础资料")
    assert "不更新奶量或生长记录" in contract.description
    assert "在对话中提供需要持久化的新资料" in contract.description
    assert "更正现有资料或要求清空资料" in contract.description
    assert "operation=update" not in contract.description
    schema = contract.input_schema
    assert "预产期" in schema["properties"]["mother"]["properties"]["estimated_due_date"]["description"]
    assert schema["additionalProperties"] is False
    assert "minProperties" not in schema
    assert "required" not in schema
    assert schema["anyOf"] == [
        {"type": "object", "required": ["mother"]},
        {"type": "object", "required": ["infants"]},
        {"type": "object", "required": ["current_infants"]},
    ]
    assert set(schema["properties"]) == {
        "mother",
        "infants",
        "current_infants",
    }
    assert {
        "preferred_name",
        "age",
        "estimated_due_date",
        "delivery_count",
        "current_delivery_method",
        "actual_delivery_date",
        "has_cesarean_history",
        "current_feeding_mode",
    } == set(schema["properties"]["mother"]["properties"])
    assert schema["properties"]["infants"]["items"]["required"] == ["infant_id"]
    assert schema["properties"]["current_infants"]["items"]["required"] == [
        "infant_id",
        "birth_order",
    ]
    for section in ("mother", "infants", "current_infants"):
        section_schema = schema["properties"][section]
        assert section_schema.get("description"), f"{section} lacks a description"
        fields = (
            section_schema["properties"]
            if section_schema["type"] == "object"
            else section_schema["items"]["properties"]
        )
        for field_name, field_schema in fields.items():
            assert field_schema.get("description"), f"{section}.{field_name} lacks a description"

    output_schema = contract.output_schema
    assert output_schema is not None
    assert output_schema["properties"]["action_type"]["enum"] == [
        "profile.update",
        "profile.current_infants.replace",
    ]
    assert output_schema["properties"]["profile"]["anyOf"][0]["$ref"].endswith(
        "MaternalInfantProfileReadOutput"
    )
    assert set(output_schema["required"]) == {
        "action_id",
        "action_type",
        "action_status",
        "requires_confirmation",
        "confirmation_policy",
        "user_visible",
        "write_succeeded",
        "preview_payload",
    }
    for field_name, field_schema in output_schema["properties"].items():
        assert field_schema.get("description"), f"update output {field_name} lacks a description"
    for definition in output_schema["$defs"].values():
        for field_name, field_schema in definition.get("properties", {}).items():
            assert field_schema.get("description"), f"update output {field_name} lacks a description"


def test_profile_update_contract_rejects_an_empty_update() -> None:
    schema = default_tool_registry().get("profile_update").input_schema

    with pytest.raises(ApiError) as exc_info:
        validate_tool_input(schema=schema, value={})

    assert exc_info.value.code == "tool_input_invalid"
