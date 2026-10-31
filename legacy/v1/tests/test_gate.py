import unittest

from src.core.gate import decide


class GateTests(unittest.TestCase):
    def test_prohibited_request_is_blocked_before_knowledge(self):
        result = decide("Can you declare a lower value on the invoice?", [])
        self.assertEqual(result["level"], "D")

    def test_no_evidence_escalates_instead_of_blocking_as_policy_violation(self):
        result = decide("What is the delivery time?", [])
        # 交期属于动态事实（delivery 命中词表）→ 需工具核验而非转人工
        self.assertEqual(result["level"], "B")
        self.assertTrue(result["need_tool"])

    def test_unrelated_question_without_evidence_goes_to_human(self):
        result = decide("How do I open a new branch office?", [])
        self.assertEqual(result["level"], "C")
        self.assertIn("转人工", result["reason"])

    def test_price_requires_live_fact_tool(self):
        result = decide("What is the price for this model?", [{
            "risk_level": "low", "action_level": "B"
        }])
        self.assertEqual(result["level"], "B")
        self.assertTrue(result["need_tool"])

    def test_price_without_knowledge_still_routes_to_fact_tool(self):
        result = decide("What is the price for JC-1?", [])
        self.assertEqual(result["level"], "B")
        self.assertTrue(result["need_tool"])


if __name__ == "__main__":
    unittest.main()
