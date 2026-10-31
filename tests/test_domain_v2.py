"""V2 domain-rule regression tests."""
from knowhow.domain import (
    answer_decision,
    detect_language,
    edit_ratio,
    language_mismatch,
)
from knowhow.access import ROLE_TEMPLATES, valid_permissions
from knowhow.services.retrieval import weighted_rrf


def test_detect_language_and_mismatch():
    assert detect_language("牵引绳有哪些") == "zh"
    assert language_mismatch(
        "牵引绳有哪些",
        "Hello. Tell me which product line you are looking for today.",
    )


def test_no_knowledge_always_escalates():
    decision = answer_decision("公司有哪些认证", [])
    assert decision.status == "escalated"
    assert decision.risk == "high"


def test_high_risk_terms_escalate_even_with_knowledge():
    decision = answer_decision(
        "可以保证终身保修吗",
        [{"risk_level": "low"}],
    )
    assert decision.status == "escalated"
    assert "负责人" in decision.reason


def test_dynamic_fact_is_draft_not_auto_send():
    decision = answer_decision(
        "500 个的价格是多少",
        [{"risk_level": "low"}],
    )
    assert decision.status == "draft"
    assert decision.risk == "medium"


def test_edit_ratio():
    assert edit_ratio("hello", "hello") == 0
    assert edit_ratio("hello", "completely different") > 0.5


def test_sales_role_has_no_publish_or_cross_team_permission():
    sales = set(ROLE_TEMPLATES["sales"]["permissions"])
    assert "ask.use" in sales
    assert "knowledge.publish" not in sales
    assert "data.all" not in sales


def test_unknown_permissions_are_discarded():
    assert valid_permissions(["ask.use", "root.everything"]) == ["ask.use"]


def test_weighted_rrf_merges_vector_and_lexical_results():
    vector = [{"id": 1, "title": "semantic"}, {"id": 2, "title": "both"}]
    lexical = [{"id": 2, "title": "both"}, {"id": 3, "title": "exact"}]
    result = weighted_rrf(vector, lexical, limit=3)
    assert [item["id"] for item in result][0] == 2
    assert set(result[0]["matched_by"]) == {"vector", "lexical"}
    assert all("rrf_score" in item for item in result)

