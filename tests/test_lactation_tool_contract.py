from app.agents.cozymate.tools import default_tool_registry
from app.agents.cozymate.tools.namespaces import default_tool_namespace_registry


def test_lactation_context_read_replaces_raw_growth_history_in_milk_namespace() -> None:
    registry = default_tool_registry()
    contract = registry.get("lactation_context_read")
    namespace = default_tool_namespace_registry(registry).get("milk_management")

    assert contract.domain == "profiles"
    assert contract.effect_scope == "none"
    assert contract.input_schema == {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }
    assert "出生时性别" in contract.description
    assert "lactation_context_read" in namespace.tool_contracts
    assert "records_growth_read" not in namespace.tool_contracts


def test_lactation_context_read_declares_described_nested_output_schema() -> None:
    contract = default_tool_registry().get("lactation_context_read")

    assert contract.output_schema is not None
    assert contract.output_schema["additionalProperties"] is False
    assert set(contract.output_schema["required"]) == {
        "as_of_date",
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

    assert "累计分娩次数" in mother["properties"]["delivery_count"]["description"]
    assert "分娩当天为 0" in mother["properties"]["postpartum_days"]["description"]
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
