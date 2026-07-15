import pytest

from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.health_guidance import (
    HEALTH_GUIDANCE_ALLOWED_DOMAINS,
    health_guidance_request_context_lines,
    needs_breast_triage_first,
    should_require_complex_health_web_search,
    should_use_complex_health_web_search,
)


@pytest.mark.parametrize(
    "message",
    [
        "产后恶露持续很多天正常吗？",
        "宝宝黄疸一直不退怎么办？",
        "乳汁电导率持续升高可能是什么原因？",
        "哺乳期用药会不会影响宝宝？",
    ],
)
def test_complex_health_questions_require_controlled_web_search(message: str) -> None:
    assert should_use_complex_health_web_search(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "宝宝黄疸一直不退怎么办？",
        "乳汁电导率持续升高可能是什么原因？",
        "哺乳期用药会不会影响宝宝？",
    ],
)
def test_evidence_sensitive_health_questions_require_web_search(message: str) -> None:
    assert should_require_complex_health_web_search(message) is True


def test_low_risk_health_update_can_continue_without_mandatory_web_search() -> None:
    message = "没有出血或发烧，疼痛也没有加重，宝宝胎动正常。今天散步后只是有一点轻微牵拉感。"

    assert should_use_complex_health_web_search(message) is True
    assert should_require_complex_health_web_search(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "帮我整理待产包清单",
        "制定孕期计划",
        "后台奶量分析提醒后的自动接续 奶量分析上下文",
        "胎动明显减少怎么办",
    ],
)
def test_product_flows_and_urgent_red_flags_do_not_require_health_web_search(message: str) -> None:
    assert should_use_complex_health_web_search(message) is False


def test_first_breast_lump_turn_suppresses_web_search_until_triage_is_answered() -> None:
    first_turn = "乳房有硬块而且疼，怎么办？"
    answered_turn = "乳房有硬块，但是没有发烧、寒战，也没有红肿，接下来怎么办？"

    assert needs_breast_triage_first(first_turn) is True
    assert should_use_complex_health_web_search(first_turn) is True
    assert needs_breast_triage_first(answered_turn) is False
    assert should_use_complex_health_web_search(answered_turn) is True
    assert "不需要 web_search" in "\n".join(health_guidance_request_context_lines(first_turn))
    assert "优先使用 web_search 检索" in "\n".join(health_guidance_request_context_lines(answered_turn))


def test_milk_intake_field_explanation_stays_in_loaded_milk_service() -> None:
    assert (
        should_use_complex_health_web_search(
            "为什么要问尿布数量？",
            loaded_skill_ids=["milk-management"],
        )
        is False
    )


def test_health_web_search_allowlist_contains_only_professional_sources() -> None:
    assert {
        "www.who.int",
        "www.nice.org.uk",
        "www.ncbi.nlm.nih.gov",
        "www.bfmed.org",
        "www.cdc.gov",
        "www.acog.org",
        "www.nhc.gov.cn",
    } <= set(HEALTH_GUIDANCE_ALLOWED_DOMAINS)
    assert all("momcozy" not in domain and "google" not in domain for domain in HEALTH_GUIDANCE_ALLOWED_DOMAINS)
