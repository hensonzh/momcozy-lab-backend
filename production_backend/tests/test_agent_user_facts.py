import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from production_backend.app.modules.agent_runtime.facts.catalog import (
    FACT_CATALOG_VERSION,
    FORM_FIELD_MAPPINGS,
    facts_to_form_defaults,
    form_values_to_fact_inputs,
)
from production_backend.app.modules.agent_runtime.facts.extraction import (
    FACT_EXTRACTOR_RESPONSE_FORMAT,
    AgentFactExtractor,
    FactExtractionContext,
)
from production_backend.app.modules.agent_runtime.facts.service import (
    AgentFactService,
    FactInput,
)
from production_backend.app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, ScriptedSdkBackend, scripted_sdk_response
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools.legacy_artifacts import (
    BIRTH_PLAN_FORM_FIELDS,
    HOSPITAL_BAG_FORM_FIELDS,
    birth_plan_form_result,
)
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools.pregnancy_plan_flow import (
    PREGNANCY_PLAN_INTAKE_FIELDS,
)


def test_fact_catalog_covers_every_birth_prep_form_field() -> None:
    assert set(FORM_FIELD_MAPPINGS["birth_journey_basic_info_intake"]) == {
        field["id"] for field in PREGNANCY_PLAN_INTAKE_FIELDS
    }
    assert set(FORM_FIELD_MAPPINGS["hospital_bag_intake"]) == {field["id"] for field in HOSPITAL_BAG_FORM_FIELDS}
    assert set(FORM_FIELD_MAPPINGS["birth_plan_card_intake"]) == {field["id"] for field in BIRTH_PLAN_FORM_FIELDS}


def test_birth_plan_form_exposes_top_level_and_field_defaults() -> None:
    form = birth_plan_form_result({"default_values": {"due_date_or_week": "30周"}})["form"]

    assert form["default_values"] == {"due_date_or_week": "30周"}
    due_field = next(field for field in form["fields"] if field["id"] == "due_date_or_week")
    assert due_field["default_value"] == "30周"


def test_fact_catalog_maps_shared_birth_prep_fields_between_forms() -> None:
    inputs = form_values_to_fact_inputs(
        form_id="birth_journey_basic_info_intake",
        values={
            "current_week": "28+3周",
            "age": 36,
            "fetus_count": "多胎",
            "birth_path": "还没确定",
            "birth_hospital": "深圳市妇幼",
            "doctor_notes": "注意血糖",
        },
    )

    values = {item.fact_key: item.value for item in inputs}
    assert values == {
        "profile.age": 36,
        "pregnancy.birth_path": "还没确定",
        "pregnancy.birth_setting": "深圳市妇幼",
        "pregnancy.doctor_notes": ["注意血糖"],
        "pregnancy.due_date_or_week": "28+3周",
        "pregnancy.fetus_count": "多胎",
    }

    hospital_defaults = facts_to_form_defaults(
        form_id="hospital_bag_intake",
        facts=values,
    )
    assert hospital_defaults == {
        "birth_path": "还不确定",
        "due_date_or_week": "28+3周",
        "fetus_count": "三胎及以上",
        "pregnancy_history_or_notes": ["注意血糖"],
    }


def test_fact_catalog_adapts_unknown_enums_and_omits_incompatible_select_defaults() -> None:
    inputs = form_values_to_fact_inputs(
        form_id="birth_journey_basic_info_intake",
        values={"fetus_count": "不确定/暂不说", "first_birth": "不确定/暂不说"},
    )
    facts = {item.fact_key: item.value for item in inputs}

    assert facts == {"pregnancy.fetus_count": "不确定", "pregnancy.first_birth": "还没确定"}
    assert facts_to_form_defaults(form_id="hospital_bag_intake", facts=facts) == {"fetus_count": "不确定"}
    assert facts_to_form_defaults(
        form_id="hospital_bag_intake",
        facts={"pregnancy.support_person": "伴侣陪产并参与决定"},
    ) == {}


def test_fact_service_applies_newer_explicit_fact_and_rejects_stale_or_uncertain_updates() -> None:
    owner_user_id = uuid4()
    repository = FakeFactRepository()
    service = AgentFactService(repository=repository)
    older = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    newer = older + timedelta(minutes=1)

    first = asyncio.run(
        service.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=[FactInput("profile.age", 36, "explicit", "我36岁")],
            source_type="conversation",
            source_id="message-new",
            observed_at=newer,
        )
    )
    stale = asyncio.run(
        service.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=[FactInput("profile.age", 35, "explicit", "我35岁")],
            source_type="conversation",
            source_id="message-old",
            observed_at=older,
        )
    )
    uncertain = asyncio.run(
        service.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=[FactInput("pregnancy.fetus_count", "双胎", "uncertain", "医生说可能双胎")],
            source_type="conversation",
            source_id="message-uncertain",
            observed_at=newer + timedelta(minutes=1),
        )
    )

    assert (first.applied_count, stale.applied_count, uncertain.applied_count) == (1, 0, 0)
    assert repository.values == {"profile.age": 36}


def test_fact_service_form_submission_overrides_conversation_at_same_time() -> None:
    owner_user_id = uuid4()
    repository = FakeFactRepository()
    service = AgentFactService(repository=repository)
    observed_at = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)

    asyncio.run(
        service.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=[FactInput("pregnancy.birth_path", "顺产", "explicit", "我想顺产")],
            source_type="conversation",
            source_id="message-1",
            observed_at=observed_at,
        )
    )
    result = asyncio.run(
        service.sync_form_submission(
            owner_user_id=owner_user_id,
            form_id="hospital_bag_intake",
            values={"birth_path": "剖宫产"},
            submission_id="submission-1",
            observed_at=observed_at,
        )
    )

    assert result.applied_count == 1
    assert repository.values["pregnancy.birth_path"] == "剖宫产"


def test_fact_extractor_uses_strict_schema_and_drops_non_explicit_or_unknown_candidates() -> None:
    message_id = uuid4()
    run_id = uuid4()
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text=json.dumps(
                    {
                        "facts": [
                            {
                                "fact_key": "profile.age",
                                "operation": "set",
                                "value": 35,
                                "certainty": "explicit",
                                "evidence": "我不是36，是35",
                            },
                            {
                                "fact_key": "pregnancy.fetus_count",
                                "operation": "set",
                                "value": "双胎",
                                "certainty": "uncertain",
                                "evidence": "医生说可能双胎",
                            },
                            {
                                "fact_key": "unknown.key",
                                "operation": "set",
                                "value": "x",
                                "certainty": "explicit",
                                "evidence": "x",
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        ]
    )
    extractor = AgentFactExtractor(model_runner=OpenAIAgentsSdkRunner(backend=backend))

    facts = asyncio.run(
        extractor.extract(
            context=FactExtractionContext(
                owner_user_id=uuid4(),
                run_id=run_id,
                source_message_id=message_id,
                source_text="我不是36，是35。医生说可能双胎，还没确认。",
                recent_dialogue=(("user", "我不是36，是35。医生说可能双胎，还没确认。"),),
                existing_facts={},
                observed_at=datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
            )
        )
    )

    assert backend.requests[0].tool_names == ()
    assert backend.requests[0].prompt_version == "turn-fact-extractor-v1"
    assert backend.requests[0].response_text_format == FACT_EXTRACTOR_RESPONSE_FORMAT
    payload = json.loads(backend.requests[0].model_input[0]["content"])
    assert payload["field_catalog_version"] == FACT_CATALOG_VERSION
    assert facts == [FactInput("profile.age", 35, "explicit", "我不是36，是35")]


def test_fact_extractor_rejects_evidence_not_present_in_current_user_message() -> None:
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text=json.dumps(
                    {
                        "facts": [
                            {
                                "fact_key": "profile.age",
                                "operation": "set",
                                "value": 35,
                                "certainty": "explicit",
                                "evidence": "我35岁",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        ]
    )
    extractor = AgentFactExtractor(model_runner=OpenAIAgentsSdkRunner(backend=backend))

    facts = asyncio.run(
        extractor.extract(
            context=FactExtractionContext(
                owner_user_id=uuid4(),
                run_id=uuid4(),
                source_message_id=uuid4(),
                source_text="我朋友35岁",
                recent_dialogue=(("user", "我朋友35岁"),),
                existing_facts={},
                observed_at=datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
            )
        )
    )

    assert facts == []


class FakeFactRepository:
    def __init__(self) -> None:
        self.records = {}
        self.values = {}

    async def list_active(self, *, owner_user_id):
        return list(self.records.values())

    async def apply_fact(
        self,
        *,
        owner_user_id,
        fact_key,
        value,
        source_type,
        source_id,
        source_priority,
        observed_at,
        evidence,
        catalog_version,
    ):
        current = self.records.get(fact_key)
        if current is not None:
            current_order = (current.observed_at, current.source_priority)
            incoming_order = (observed_at, source_priority)
            if incoming_order <= current_order:
                return False
        record = FakeFactRecord(
            fact_key=fact_key,
            value=value,
            source_priority=source_priority,
            observed_at=observed_at,
        )
        self.records[fact_key] = record
        self.values[fact_key] = value
        return True


class FakeFactRecord:
    def __init__(self, *, fact_key, value, source_priority, observed_at) -> None:
        self.fact_key = fact_key
        self.value = value
        self.source_priority = source_priority
        self.observed_at = observed_at
