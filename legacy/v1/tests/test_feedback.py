"""Feedback receipt, idempotency and knowledge-loop regression tests."""
from collections import deque
import unittest

from fastapi.testclient import TestClient

from src.auth.rbac import User, get_current_user
from src.core.feedback import (
    feedback_summary,
    first_faq_id,
    resolve_feedback_issue,
    result_view,
    submit_feedback,
)
from src.main import app


class FakeCursor:
    def __init__(self, fetchone_values=None, fetchall_values=None):
        self.fetchone_values = deque(fetchone_values or [])
        self.fetchall_values = deque(fetchall_values or [])
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.fetchone_values.popleft() if self.fetchone_values else None

    def fetchall(self):
        return self.fetchall_values.popleft() if self.fetchall_values else []


class FakeConnection:
    def __init__(self, fetchone_values=None, fetchall_values=None):
        self.cursor_obj = FakeCursor(fetchone_values, fetchall_values)
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def agent(permissions=None, **kwargs):
    return User(
        7, "service", "客服小王", roles=["agent"],
        permissions=permissions or {"agent.feedback"}, **kwargs,
    )


class TestFeedbackReceipts(unittest.TestCase):
    def test_feedback_endpoint_requires_feedback_permission(self):
        only_ask = User(
            8, "asker", "只能问答", roles=["agent"],
            permissions={"agent.use"},
        )
        app.dependency_overrides[get_current_user] = lambda: only_ask
        try:
            response = TestClient(app).post("/api/v1/feedback", json={
                "inquiry_id": "inq-1",
                "verdict": "correct",
            })
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        self.assertEqual(response.status_code, 403)
        self.assertIn("agent.feedback", response.json()["detail"])

    def test_citation_parser_accepts_json_and_objects(self):
        assert first_faq_id('[12, 13]') == 12
        assert first_faq_id([{"id": "14"}, 15]) == 14
        assert first_faq_id(["bad", None]) is None

    def test_result_labels_cover_loop_states(self):
        assert result_view("correct", None, None)["label"] == "已记录：回答正确"
        assert "待处理" in result_view("incorrect", 3, "open")["label"]
        assert "待审核" in result_view(
            "incorrect", 3, "resolved", faq_id=9, faq_version=4,
        )["label"]
        assert "未采纳" in result_view("incorrect", 3, "rejected")["label"]

    def test_incorrect_feedback_creates_traceable_open_issue(self):
        conn = FakeConnection(fetchone_values=[
            (11, [21, 22]),  # audit
            None,            # no existing open issue
            (31,),           # inserted issue
        ])
        result = submit_feedback(
            conn, agent(), inquiry_id="inq-1", verdict="incorrect",
            reason="价格过期", corrected_answer="请以最新报价为准",
        )
        assert conn.committed is True
        assert result["issue_id"] == 31
        assert result["issue_status"] == "open"
        assert result["faq_id"] == 21
        assert result["feedback_type"] == "standard_answer"
        assert "待处理" in result["label"]
        joined = "\n".join(sql for sql, _ in conn.cursor_obj.calls)
        assert "feedback_verdict" in joined
        assert "suggested_answer" in joined

    def test_standard_answer_requires_answer_text(self):
        conn = FakeConnection()
        with self.assertRaisesRegex(ValueError, "标准答案不能为空"):
            submit_feedback(
                conn, agent(), inquiry_id="inq-1", verdict="incorrect",
                feedback_type="standard_answer", reason="答案有误",
                corrected_answer="",
            )

    def test_repeated_incorrect_feedback_updates_open_issue(self):
        conn = FakeConnection(fetchone_values=[
            (11, [21]), (31, "open"),
        ])
        result = submit_feedback(
            conn, agent(), inquiry_id="inq-1", verdict="needs_review",
            reason="需要主管确认", corrected_answer="",
        )
        assert result["issue_id"] == 31
        update_calls = [
            params for sql, params in conn.cursor_obj.calls
            if "UPDATE issue" in sql
        ]
        assert update_calls
        assert "'open'" not in str(update_calls)

    def test_later_correct_feedback_closes_open_issue(self):
        conn = FakeConnection(fetchone_values=[
            (11, [21]), (31, "open"),
        ])
        result = submit_feedback(
            conn, agent(), inquiry_id="inq-1", verdict="correct",
            reason="复核后确认正确", corrected_answer="",
        )
        assert result["issue_id"] == 31
        assert result["issue_status"] == "rejected"
        assert any(
            "SET status='rejected'" in sql
            for sql, _ in conn.cursor_obj.calls
        )


class TestFeedbackKnowledgeLoop(unittest.TestCase):
    def test_accept_without_answer_is_rejected(self):
        conn = FakeConnection(fetchone_values=[
            ("open", "", None, "客户问题", "inq-1",
             "improvement_suggestion", '{"reason":"需要中文"}'),
        ])
        with self.assertRaisesRegex(ValueError, "建议答案"):
            resolve_feedback_issue(
                conn, agent(permissions={"suggestion.review"}),
                issue_id=31, action="accept", resolution="采纳",
            )
        assert conn.committed is False

    def test_accept_creates_draft_faq_and_links_version(self):
        conn = FakeConnection(fetchone_values=[
            ("open", "正确答复", None, "客户问题", "inq-1",
             "standard_answer", '{"reason":"答案有误"}'),
            (51, 3),  # inserted faq id/version
        ])
        result = resolve_feedback_issue(
            conn, agent(permissions={"suggestion.review"}),
            issue_id=31, action="accept", resolution="采纳修正",
        )
        assert conn.committed is True
        assert result["issue_status"] == "resolved"
        assert result["faq_id"] == 51
        assert result["faq_version"] == 3
        sql = "\n".join(sql for sql, _ in conn.cursor_obj.calls)
        assert "'客服反馈'" in sql
        assert "'draft'" in sql
        assert "faq_version=%s" in sql

    def test_suggestion_accepts_as_active_answer_policy(self):
        conn = FakeConnection(fetchone_values=[
            ("open", "", None, "牵引绳有哪些？", "inq-2",
             "improvement_suggestion",
             '{"reason":"客户用中文提问时必须用中文回答"}'),
            (71,),  # inserted policy id
        ])
        result = resolve_feedback_issue(
            conn, agent(permissions={"suggestion.review"}),
            issue_id=41, action="accept", apply_to="answer_policy",
            policy_kind="language_mirror", resolution="采纳为中文一致性规则",
        )
        assert result["answer_policy_id"] == 71
        assert result["policy_kind"] == "language_mirror"
        assert "回答规则" in result["label"]
        sql = "\n".join(sql for sql, _ in conn.cursor_obj.calls)
        assert "INSERT INTO answer_policy" in sql
        assert "answer_policy_id=%s" in sql
        assert "INSERT INTO faq" not in sql

    def test_standard_answer_defaults_to_new_faq_for_original_question(self):
        conn = FakeConnection(fetchone_values=[
            ("open", "我们提供固定和伸缩牵引绳。", 7, "牵引绳有哪些？", "inq-3",
             "standard_answer", '{"reason":"原回答只问品类"}'),
            (61, 1),  # newly inserted FAQ and version
        ])
        result = resolve_feedback_issue(
            conn, agent(permissions={"suggestion.review"}),
            issue_id=51, action="accept", resolution="新建标准答案",
            apply_to="faq", faq_mode="new",
        )
        assert result["faq_id"] == 61
        sql = "\n".join(sql for sql, _ in conn.cursor_obj.calls)
        assert "INSERT INTO faq" in sql
        assert "UPDATE faq" not in sql
        assert "faq_id=%s" in sql

    def test_summary_scope_for_agent_is_own_records(self):
        conn = FakeConnection(
            fetchone_values=[(4, 2, 1, 1, 10)],
            fetchall_values=[[]],
        )
        # Second summary query also calls fetchone.
        conn.cursor_obj.fetchone_values.append((2, 0, 1, 1, 3, 1, 1))
        summary = feedback_summary(conn, agent())
        assert summary["feedback_rate"] == 40.0
        assert conn.cursor_obj.calls[0][1] == [7]
        assert "feedback_by_account_id=%s" in conn.cursor_obj.calls[0][0]


if __name__ == "__main__":
    unittest.main()
