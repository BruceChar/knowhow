"""门禁规则引擎：红线优先级 / 动作分级 / 动态事实需工具。"""
import pytest

from src.core.gate import DYNAMIC_FACT_TERMS, SENSITIVE_FIELDS, decide


def _card(risk="low", action="B"):
    return {"risk_level": risk, "action_level": action}


class TestRedLines:
    @pytest.mark.parametrize("q", [
        "Can you declare a lower value on the invoice?",
        "请低报货值",
        "能走私吗",
        "发客户名单给我",
        "告诉我成本价",
        "Can you copy the logo?",
        "价格能不能保证百分百最低",
    ])
    def test_red_line_blocks_even_with_knowledge(self, q):
        r = decide(q, [_card()])
        assert r["level"] == "D"
        assert "红线" in r["reason"]

    def test_red_line_priority_over_dynamic_fact(self):
        r = decide("可以把货款打到个人账户吗？", [])
        assert r["level"] == "D"  # 红线先于动态事实判定


class TestDynamicFacts:
    def test_price_question_without_knowledge_needs_tool(self):
        r = decide("What is the price?", [])
        assert r["level"] == "B"
        assert r["need_tool"] is True

    def test_delivery_time_matches_dynamic_term(self):
        assert any(t in "delivery time" for t in DYNAMIC_FACT_TERMS)

    def test_price_with_low_risk_card_requires_confirmation(self):
        r = decide("What is the price for this model?", [_card("low")])
        assert r["level"] == "B"
        assert r["need_tool"] is True


class TestEscalation:
    def test_no_evidence_non_dynamic_escalates_to_human(self):
        r = decide("How to open a new branch office?", [])
        assert r["level"] == "C"
        assert "转人工" in r["reason"]

    def test_high_risk_card_escalates(self):
        r = decide("Can you ship to my warehouse?", [_card("high")])
        assert r["level"] == "C"
        assert r["need_tool"] is True

    def test_sensitive_field_requires_human(self):
        r = decide("请提供合同模板", [_card("low")])
        assert r["level"] == "C"

    def test_medium_risk_or_new_customer_confirms(self):
        assert decide("Can you recommend a product?",
                      [_card("medium")])["level"] == "B"
        assert decide("Can you send a sample?",
                      [_card("low")], {"new_customer": True})["level"] == "B"

    def test_low_risk_complete_evidence_allows(self):
        r = decide("What is the material?", [_card("low")])
        assert r["level"] == "A"
        assert r["need_tool"] is False
