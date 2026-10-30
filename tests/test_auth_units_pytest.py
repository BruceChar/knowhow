"""认证/权限纯函数：pbkdf2 哈希、评测命中判定、数据权限子句。"""
import pytest

from src.auth.auth import hash_password, verify_password
from src.auth.rbac import User, data_permission_clause
from src.core.eval_runner import _hit, _ideal_list, _norm


class TestPasswordHash:
    def test_hash_verify_roundtrip(self):
        h = hash_password("admin")
        assert verify_password("admin", h) is True

    def test_wrong_password_rejected(self):
        h = hash_password("admin")
        assert verify_password("wrong", h) is False

    def test_salt_makes_hashes_unique(self):
        assert hash_password("same") != hash_password("same")

    def test_malformed_hash_rejected(self):
        assert verify_password("x", "not-a-valid-format") is False
        assert verify_password("x", "md5$0$00$00") is False


class TestEvalHit:
    def test_norm_tolerates_quotes_and_case(self):
        assert _norm('"What is your MOQ?"') == _norm("what is your moq?")

    def test_ideal_list_single_and_multi(self):
        assert _ideal_list({"ideal_hit_question": "A"}) == ["A"]
        assert _ideal_list({"ideal_hit_questions": ["A", "B"]}) == ["A", "B"]

    def test_hit_matches_any(self):
        assert _hit(["x", '"unit price?"'], ["unit price?"]) is True
        assert _hit(["a", "b"], ["c"]) is False


class TestDataScope:
    def test_agent_scope_excludes_confidential(self):
        clause = data_permission_clause(User("u", "Agent", "agent"), "f")
        assert "public" in clause and "internal" in clause
        assert "confidential" not in clause

    def test_admin_scope_covers_all(self):
        clause = data_permission_clause(
            User("u", "Admin", "admin", data_scope="full"), "f")
        assert "confidential" in clause

    def test_standard_scope_excludes_confidential(self):
        clause = data_permission_clause(
            User("u", "Editor", "editor", data_scope="standard"), "f")
        assert "confidential" not in clause
