import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.agent_runtime.facts.catalog import (
    FACT_CATALOG_VERSION,
    FORM_FIELD_MAPPINGS,
    facts_to_form_defaults,
    form_values_to_fact_inputs,
)
from app.modules.agent_runtime.facts.extraction import (
    FACT_EXTRACTOR_RESPONSE_FORMAT,
    AgentFactExtractor,
    FactExtractionContext,
)
from app.modules.agent_runtime.facts.service import (
    AgentFactService,
    FactInput,
)
from app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, ScriptedSdkBackend, scripted_sdk_response
from app.modules.agent_runtime.agents.cozymate_service_agent.tools.legacy_artifacts import (
    BIRTH_PLAN_FORM_FIELDS,
    HOSPITAL_BAG_FORM_FIELDS,
    birth_plan_form_result,
)
from app.modules.agent_runtime.agents.cozymate_service_agent.tools.pregnancy_plan_flow import (
    PREGNANCY_PLAN_INTAKE_FIELDS,
)


def test_fact_catalog_covers_every_birth_prep_form_field() -> None:
    assert set(FORM_FIELD_MAPPINGS["birth_journey_basic_info_intake"]) == {
        field["id"] for field in PREGNANCY_PLAN_INTAKE_FIELDS
    }
    assert set(FORM_FIELD_MAPPINGS["hospital_bag_intake"]) == {field["id"] for field in HOSPITAL_BAG_FORM_FIELDS}
    assert set(FORM_FIELD_MAPPINGS["birth_plan_card_intake"]) == {field["id"] for field in BIRTH_PLAN_FORM_FIELDS}
    candidate_keys = FACT_EXTRACTOR_RESPONSE_FORMAT["schema"]["properties"]["facts"]["items"]["properties"][
        "fact_key"
    ]["enum"]
    assert candidate_keys == sorted(
        {
            "profile.age",
            "pregnancy.due_date_or_week",
            "pregnancy.fetus_count",
            "pregnancy.first_birth",
            "pregnancy.birth_path",
            "pregnancy.feeding_intention",
            "birth_plan.emergency_authorization",
        }
    )


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
            inputs=[FactInput("profile.age", 36, "explicit", "我36岁", "self")],
            source_type="conversation",
            source_id="message-new",
            observed_at=newer,
        )
    )
    stale = asyncio.run(
        service.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=[FactInput("profile.age", 35, "explicit", "我35岁", "self")],
            source_type="conversation",
            source_id="message-old",
            observed_at=older,
        )
    )
    uncertain = asyncio.run(
        service.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=[FactInput("pregnancy.fetus_count", "双胎", "uncertain", "医生说可能双胎", "current_pregnancy")],
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
            inputs=[FactInput("pregnancy.birth_path", "顺产", "explicit", "我想顺产", "current_pregnancy")],
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


def test_fact_service_conversation_candidate_never_overrides_verified_form_even_when_newer() -> None:
    owner_user_id = uuid4()
    repository = FakeFactRepository()
    service = AgentFactService(repository=repository)
    verified_at = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)

    asyncio.run(
        service.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=[FactInput("pregnancy.birth_path", "剖宫产", "explicit", "表单选择剖宫产")],
            source_type="verified_form",
            source_id="submission-1",
            observed_at=verified_at,
        )
    )
    candidate = asyncio.run(
        service.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=[FactInput("pregnancy.birth_path", "顺产", "explicit", "我想顺产", "current_pregnancy")],
            source_type="conversation_candidate",
            source_id="message-2",
            observed_at=verified_at + timedelta(days=1),
        )
    )

    assert candidate.applied_count == 1
    assert repository.values["pregnancy.birth_path"] == "剖宫产"


def test_fact_service_rejects_wrong_subject_even_if_candidate_key_and_value_are_valid() -> None:
    repository = FakeFactRepository()
    service = AgentFactService(repository=repository)

    result = asyncio.run(
        service.apply_inputs(
            owner_user_id=uuid4(),
            inputs=[FactInput("profile.age", 35, "explicit", "我朋友35岁", "other")],
            source_type="conversation",
            source_id="message-third-party",
            observed_at=datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
        )
    )

    assert result.applied_count == 0
    assert result.rejected_count == 1
    assert repository.values == {}


def test_fact_service_clear_cancels_pending_jobs_and_audits_counts_without_values() -> None:
    owner_user_id = uuid4()
    repository = FakeFactPrivacyRepository()
    audit_service = FakeFactAuditService()
    service = AgentFactService(repository=repository, audit_service=audit_service)

    deleted_count = asyncio.run(
        service.clear_facts(
            owner_user_id=owner_user_id,
            request_id="request-clear",
        )
    )

    assert deleted_count == 2
    assert repository.cancelled_owner_user_id == owner_user_id
    assert repository.fact.value is None
    assert audit_service.calls[-1] == {
        "actor_user_id": owner_user_id,
        "actor_type": "user",
        "actor_service": "",
        "action": "agent.fact.clear",
        "resource_type": "agent_user_facts",
        "resource_id": str(owner_user_id),
        "request_id": "request-clear",
        "details": {"deleted_fact_count": 2, "cancelled_extraction_count": 2},
    }
    assert all("value" not in call and "value" not in call.get("details", {}) for call in audit_service.calls)


def test_fact_service_single_delete_cancels_pending_jobs_before_they_can_repopulate_key() -> None:
    owner_user_id = uuid4()
    repository = FakeFactPrivacyRepository()
    audit_service = FakeFactAuditService()
    service = AgentFactService(repository=repository, audit_service=audit_service)

    deleted = asyncio.run(
        service.delete_fact(
            owner_user_id=owner_user_id,
            fact_id=repository.fact.id,
            request_id="request-delete",
        )
    )

    assert deleted.status == "tombstoned"
    assert deleted.value is None
    assert repository.sibling_fact.status == "tombstoned"
    assert repository.sibling_fact.value is None
    assert repository.cancel_reason == "user_deleted_fact"
    assert {
        "deletion_reason": "user_deleted",
        "cancelled_extraction_count": 2,
    }.items() <= audit_service.calls[-1]["details"].items()

    repository.cancel_reason = ""
    replayed = asyncio.run(
        service.delete_fact(
            owner_user_id=owner_user_id,
            fact_id=repository.fact.id,
            request_id="request-delete-replay",
        )
    )

    assert replayed is repository.fact
    assert repository.cancel_reason == ""


def test_fact_service_rejects_restricted_content_smuggled_through_allowed_candidate_key() -> None:
    repository = FakeFactRepository()
    service = AgentFactService(repository=repository)

    result = asyncio.run(
        service.apply_inputs(
            owner_user_id=uuid4(),
            inputs=[
                FactInput(
                    "pregnancy.top_worries",
                    ["妊娠糖尿病会影响宝宝"],
                    "explicit",
                    "妊娠糖尿病会影响宝宝",
                    "current_pregnancy",
                )
            ],
            source_type="conversation",
            source_id="message-sensitive-smuggling",
            observed_at=datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
        )
    )

    assert (result.applied_count, result.rejected_count) == (0, 1)
    assert repository.values == {}


@pytest.mark.parametrize(
    ("source_text", "candidate"),
    [
        (
            "我妹妹今年35岁。",
            {
                "fact_key": "profile.age",
                "operation": "set",
                "value": 35,
                "subject": "self",
                "certainty": "explicit",
                "evidence": "35岁",
            },
        ),
        (
            "My friend is 35 years old.",
            {
                "fact_key": "profile.age",
                "operation": "set",
                "value": 35,
                "subject": "self",
                "certainty": "explicit",
                "evidence": "35 years old",
            },
        ),
        (
            "My cousin is 35 years old.",
            {
                "fact_key": "profile.age",
                "operation": "set",
                "value": 35,
                "subject": "self",
                "certainty": "explicit",
                "evidence": "35 years old",
            },
        ),
        (
            "Alice is 35 years old.",
            {
                "fact_key": "profile.age",
                "operation": "set",
                "value": 35,
                "subject": "self",
                "certainty": "explicit",
                "evidence": "35 years old",
            },
        ),
        (
            "我表姐今年35岁。",
            {
                "fact_key": "profile.age",
                "operation": "set",
                "value": 35,
                "subject": "self",
                "certainty": "explicit",
                "evidence": "35岁",
            },
        ),
        (
            "隔壁小王今年35岁。",
            {
                "fact_key": "profile.age",
                "operation": "set",
                "value": 35,
                "subject": "self",
                "certainty": "explicit",
                "evidence": "35岁",
            },
        ),
        (
            "我姐姐怀的是双胎。",
            {
                "fact_key": "pregnancy.fetus_count",
                "operation": "set",
                "value": "双胎",
                "subject": "current_pregnancy",
                "certainty": "explicit",
                "evidence": "双胎",
            },
        ),
        (
            "Alice这胎是双胎。",
            {
                "fact_key": "pregnancy.fetus_count",
                "operation": "set",
                "value": "双胎",
                "subject": "current_pregnancy",
                "certainty": "explicit",
                "evidence": "双胎",
            },
        ),
        (
            "小王这胎是双胎。",
            {
                "fact_key": "pregnancy.fetus_count",
                "operation": "set",
                "value": "双胎",
                "subject": "current_pregnancy",
                "certainty": "explicit",
                "evidence": "双胎",
            },
        ),
        (
            "Alice says this pregnancy is twins.",
            {
                "fact_key": "pregnancy.fetus_count",
                "operation": "set",
                "value": "twins",
                "subject": "current_pregnancy",
                "certainty": "explicit",
                "evidence": "twins",
            },
        ),
        (
            "Maybe twins, but I am not sure.",
            {
                "fact_key": "pregnancy.fetus_count",
                "operation": "set",
                "value": "双胎",
                "subject": "current_pregnancy",
                "certainty": "explicit",
                "evidence": "twins",
            },
        ),
        (
            "我今年35岁。",
            {
                "fact_key": "profile.age",
                "operation": "set",
                "value": 36,
                "subject": "self",
                "certainty": "explicit",
                "evidence": "我今年35岁",
            },
        ),
        (
            "我最担心妊娠糖尿病会影响宝宝。",
            {
                "fact_key": "pregnancy.top_worries",
                "operation": "set",
                "value": ["妊娠糖尿病会影响宝宝"],
                "subject": "current_pregnancy",
                "certainty": "explicit",
                "evidence": "妊娠糖尿病会影响宝宝",
            },
        ),
        (
            "我最担心子痫前期会影响宝宝。",
            {
                "fact_key": "pregnancy.top_worries",
                "operation": "set",
                "value": ["子痫前期会影响宝宝"],
                "subject": "current_pregnancy",
                "certainty": "explicit",
                "evidence": "子痫前期会影响宝宝",
            },
        ),
        (
            "希望医护知道我HIV阳性。",
            {
                "fact_key": "birth_plan.top_priorities",
                "operation": "set",
                "value": ["我HIV阳性"],
                "subject": "self",
                "certainty": "explicit",
                "evidence": "我HIV阳性",
            },
        ),
        (
            "我朋友的老公会帮忙照顾她。",
            {
                "fact_key": "pregnancy.support_person",
                "operation": "set",
                "value": "朋友的老公会帮忙照顾她",
                "subject": "current_pregnancy",
                "certainty": "explicit",
                "evidence": "朋友的老公会帮忙照顾她",
            },
        ),
    ],
)
def test_fact_extractor_rejects_adversarial_attribution_uncertainty_grounding_and_sensitive_smuggling(
    source_text: str,
    candidate: dict,
) -> None:
    assert _extract_scripted_candidates(source_text=source_text, candidates=[candidate]) == []


def test_fact_extractor_accepts_unambiguous_self_clause_next_to_third_party_clause() -> None:
    facts = _extract_scripted_candidates(
        source_text="我今年35岁，我妹妹今年30岁。",
        candidates=[
            {
                "fact_key": "profile.age",
                "operation": "set",
                "value": 35,
                "subject": "self",
                "certainty": "explicit",
                "evidence": "我今年35岁",
            }
        ],
    )

    assert facts == [FactInput("profile.age", 35, "explicit", "我今年35岁", "self")]


def test_fact_extractor_accepts_explicit_first_person_current_pregnancy() -> None:
    facts = _extract_scripted_candidates(
        source_text="我这胎是双胎。",
        candidates=[
            {
                "fact_key": "pregnancy.fetus_count",
                "operation": "set",
                "value": "双胎",
                "subject": "current_pregnancy",
                "certainty": "explicit",
                "evidence": "我这胎是双胎",
            }
        ],
    )

    assert facts == [FactInput("pregnancy.fetus_count", "双胎", "explicit", "我这胎是双胎", "current_pregnancy")]


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
                                "subject": "self",
                                "certainty": "explicit",
                                "evidence": "我不是36，是35",
                            },
                            {
                                "fact_key": "pregnancy.fetus_count",
                                "operation": "set",
                                "value": "双胎",
                                "subject": "current_pregnancy",
                                "certainty": "uncertain",
                                "evidence": "医生说可能双胎",
                            },
                            {
                                "fact_key": "unknown.key",
                                "operation": "set",
                                "value": "x",
                                "subject": "self",
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
                observed_at=datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
            )
        )
    )

    assert backend.requests[0].tool_names == ()
    assert backend.requests[0].prompt_version == "turn-fact-extractor-v2"
    assert backend.requests[0].response_text_format == FACT_EXTRACTOR_RESPONSE_FORMAT
    payload = json.loads(backend.requests[0].model_input[0]["content"])
    assert payload["field_catalog_version"] == FACT_CATALOG_VERSION
    assert facts == [FactInput("profile.age", 35, "explicit", "我不是36，是35", "self")]


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
                                "subject": "self",
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
                observed_at=datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
            )
        )
    )

    assert facts == []


def test_fact_extractor_rejects_third_party_fact_even_when_evidence_is_present() -> None:
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
                                "subject": "other",
                                "certainty": "explicit",
                                "evidence": "我朋友35岁",
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
                source_text="我朋友35岁，她想准备待产包。",
                recent_dialogue=(("user", "我朋友35岁，她想准备待产包。"),),
                observed_at=datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
            )
        )
    )

    assert facts == []


def test_fact_extractor_does_not_turn_sensitive_medical_notes_into_conversation_candidates() -> None:
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text=json.dumps(
                    {
                        "facts": [
                            {
                                "fact_key": "pregnancy.medical_notes",
                                "operation": "set",
                                "value": "确诊妊娠糖尿病",
                                "subject": "current_pregnancy",
                                "certainty": "explicit",
                                "evidence": "我确诊妊娠糖尿病",
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
                source_text="我确诊妊娠糖尿病，请记住。",
                recent_dialogue=(("user", "我确诊妊娠糖尿病，请记住。"),),
                observed_at=datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
            )
        )
    )

    assert facts == []


def _extract_scripted_candidates(*, source_text: str, candidates: list[dict]) -> list[FactInput]:
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text=json.dumps({"facts": candidates}, ensure_ascii=False),
            )
        ]
    )
    extractor = AgentFactExtractor(model_runner=OpenAIAgentsSdkRunner(backend=backend))
    return asyncio.run(
        extractor.extract(
            context=FactExtractionContext(
                owner_user_id=uuid4(),
                run_id=uuid4(),
                source_message_id=uuid4(),
                source_text=source_text,
                recent_dialogue=(("user", source_text),),
                observed_at=datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
            )
        )
    )


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
        fact_kind,
        value,
        source_type,
        source_id,
        sensitivity,
        observed_at,
        expires_at,
        catalog_version,
    ):
        record_key = (fact_key, fact_kind)
        current = self.records.get(record_key)
        if current is not None:
            if observed_at <= current.observed_at:
                return current, False
        record = FakeFactRecord(
            fact_key=fact_key,
            fact_kind=fact_kind,
            value=value,
            observed_at=observed_at,
        )
        self.records[record_key] = record
        self._refresh_values()
        return record, True

    async def tombstone_kind_for_owner_key(
        self,
        *,
        owner_user_id,
        fact_key,
        fact_kind,
        deleted_at,
        deletion_reason,
    ):
        record = self.records.get((fact_key, fact_kind))
        if record is None:
            return None
        record.status = "tombstoned"
        record.value = None
        self._refresh_values()
        return record

    def _refresh_values(self):
        values = {}
        for fact_key in {key for key, _kind in self.records}:
            verified = self.records.get((fact_key, "verified"))
            candidate = self.records.get((fact_key, "conversation_candidate"))
            selected = verified if verified is not None and verified.status == "active" else candidate
            if selected is not None and selected.status == "active":
                values[fact_key] = selected.value
        self.values = values


class FakeFactRecord:
    def __init__(self, *, fact_key, fact_kind, value, observed_at) -> None:
        self.id = uuid4()
        self.fact_key = fact_key
        self.fact_kind = fact_kind
        self.value = value
        self.status = "active"
        self.observed_at = observed_at


class FakeFactPrivacyRepository:
    def __init__(self) -> None:
        self.fact = FakeFactRecord(
            fact_key="profile.age",
            fact_kind="verified",
            value=36,
            observed_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        self.sibling_fact = FakeFactRecord(
            fact_key="profile.age",
            fact_kind="conversation_candidate",
            value=35,
            observed_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        self.cancelled_owner_user_id = None
        self.cancel_reason = ""

    async def get_for_owner(self, *, owner_user_id, fact_id):
        return self.fact if fact_id == self.fact.id else None

    async def cancel_pending_extractions_for_owner(self, *, owner_user_id, completed_at, reason):
        self.cancelled_owner_user_id = owner_user_id
        self.cancel_reason = reason
        return [object(), object()]

    async def tombstone_all_kinds_for_owner_key(self, *, owner_user_id, fact_key, deleted_at, deletion_reason):
        assert owner_user_id == self.cancelled_owner_user_id
        assert fact_key == self.fact.fact_key
        for fact in (self.fact, self.sibling_fact):
            fact.status = "tombstoned"
            fact.value = None
        return [self.fact, self.sibling_fact]

    async def tombstone_all_for_owner(self, *, owner_user_id, deleted_at, deletion_reason):
        assert owner_user_id == self.cancelled_owner_user_id
        assert self.cancel_reason == "user_cleared"
        assert deletion_reason == "user_deleted"
        for fact in (self.fact, self.sibling_fact):
            fact.status = "tombstoned"
            fact.value = None
        return [self.fact, self.sibling_fact]


class FakeFactAuditService:
    def __init__(self) -> None:
        self.calls = []

    async def record(self, **kwargs):
        self.calls.append(kwargs)
