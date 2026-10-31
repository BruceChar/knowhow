"""Answer-policy and offline language-guard regression tests."""
import unittest
from unittest.mock import patch

from src.core.generate import _extractive_answer, draft_reply
from src.core.policies import (
    language_guard_answer,
    language_mismatch,
    policy_instruction_text,
)


def card(answer: str) -> dict:
    return {
        "id": 4,
        "question": "What leashes do you have?",
        "answer": answer,
        "sources": ["M01-Q004"],
    }


LANGUAGE_POLICY = [{
    "id": 1,
    "name": "客户语言一致性",
    "rule": "客户用中文提问时必须用中文回答。",
    "policy_kind": "language_mirror",
}]


class TestAnswerPolicies(unittest.TestCase):
    def test_policy_text_is_injected_as_instruction(self):
        text = policy_instruction_text(LANGUAGE_POLICY)
        self.assertIn("[language_mirror]", text)
        self.assertIn("必须用中文回答", text)

    def test_chinese_question_with_english_evidence_uses_guard(self):
        question = "牵引绳有哪些？"
        english = (
            "Hello. Tell me which line you are looking at and I will take "
            "it from there."
        )
        self.assertTrue(language_mismatch(question, english))
        answer = _extractive_answer(
            question, [card(english)], None, LANGUAGE_POLICY,
        )
        self.assertIn("中文答复", answer)
        self.assertIn("知识卡4", answer)
        self.assertNotIn("Tell me which line", answer)

    def test_language_guard_requires_human_review(self):
        question = "牵引绳有哪些？"
        english = (
            "Hello. Tell me which line you are looking at and I will take "
            "it from there."
        )
        with patch.dict("os.environ", {"LLM_API_KEY": ""}, clear=False):
            result = draft_reply(
                question, [card(english)], backend="openai",
                policies=LANGUAGE_POLICY,
            )
        self.assertTrue(result["requires_human_review"])
        self.assertEqual(result["model"], "grounded-language-guard")

    def test_llm_output_is_guarded_when_policy_is_violated(self):
        class FakeProvider:
            name = "fake"
            model = "fake-llm"

            @staticmethod
            def chat(_messages, temperature=0):
                return (
                    "Hello. Tell me which line you are looking at and I "
                    "will take it from there."
                )

        question = "牵引绳有哪些？"
        with patch.dict("os.environ", {"LLM_API_KEY": "test"}, clear=False):
            with patch("src.core.generate.get_llm_provider",
                       return_value=FakeProvider()):
                result = draft_reply(
                    question, [card("English source")], backend="openai",
                    policies=LANGUAGE_POLICY,
                )
        self.assertTrue(result["requires_human_review"])
        self.assertIn("中文答复", result["answer"])
        self.assertIn("language-guard", result["model"])

    def test_english_question_keeps_grounded_extractive_answer(self):
        english = "We offer fixed leashes and retractable leashes."
        answer = _extractive_answer(
            "What leashes do you offer?", [card(english)], None,
            LANGUAGE_POLICY,
        )
        self.assertIn("fixed leashes", answer)
        self.assertIn("[来源: 知识卡4", answer)

    def test_guard_answer_does_not_invent_catalog_facts(self):
        answer = language_guard_answer([card("English only")])
        self.assertIn("人工", answer)
        self.assertIn("引用依据", answer)
        self.assertNotIn("伸缩", answer)


if __name__ == "__main__":
    unittest.main()
