from app.agents.cozymate.tools import default_tool_registry


def test_maternal_infant_profile_read_replaces_raw_growth_history_as_direct_tool() -> None:
    registry = default_tool_registry()
    contract = registry.get("maternal_infant_profile_read")
    names = set(registry.names_for_sdk())

    assert contract.domain == "profiles"
    assert contract.effect_scope == "none"
    assert contract.input_schema == {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "infant_scope": {
                "type": "string",
                "enum": ["current_delivery", "all"],
                "default": "current_delivery",
                "description": (
                    "宝宝读取范围。current_delivery 仅返回当前这次分娩的宝宝，供奶量分析使用；"
                    "all 返回当前用户的全部宝宝，供通用资料核对和选择 infant_id 使用。"
                ),
            }
        },
    }
    assert "妈妈与宝宝的基础资料" in contract.description
    assert "不返回奶量产出或摄入记录" in contract.description
    assert "maternal_infant_profile_read" in names
    assert "maternal_infant_profile_update" in names
    assert "profile_read" not in names
    assert "profile_update" not in names
    assert "lactation_context_read" not in names
    assert "records_growth_read" not in names


def test_maternal_infant_profile_read_declares_described_nested_output_schema() -> None:
    contract = default_tool_registry().get("maternal_infant_profile_read")

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


def test_maternal_infant_profile_update_is_the_described_profile_write_superset() -> None:
    contract = default_tool_registry().get("maternal_infant_profile_update")

    assert contract.domain == "profiles"
    assert contract.effect_scope == "user_resource"
    assert "action_type" not in type(contract).model_fields
    assert "maternal_infant_profile_read" in contract.description
    assert "预产期" in contract.description
    schema = contract.input_schema
    assert schema["additionalProperties"] is False
    assert schema["minProperties"] == 1
    assert set(schema["properties"]) == {
        "mother",
        "infants",
        "current_infants",
        "idempotency_key",
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
        fields = (
            section_schema["properties"]
            if section_schema["type"] == "object"
            else section_schema["items"]["properties"]
        )
        for field_name, field_schema in fields.items():
            assert field_schema.get("description"), f"{section}.{field_name} lacks a description"

    output_schema = contract.output_schema
    assert output_schema is not None
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
