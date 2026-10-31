"""API 冒烟测试：登录/权限隔离/角色管理/改密/管理员停用保护。

依赖 8000 端口服务在线；服务不可达时整组 skip（-m "not integration" 可排除）。
用标准库 urllib 实现，不引入额外依赖。
"""
import json
import urllib.error
import urllib.request

import pytest

BASE = "http://localhost:8000"

pytestmark = pytest.mark.integration


def _healthy():
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not _healthy(),
                                 reason="8000 服务未运行（跳过集成冒烟）")]

SESSION_COOKIE = "knowhow_session"


class ApiClient:
    def __init__(self, username, password):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor())
        resp = self.post("/auth/login", {"username": username,
                                          "password": password})
        code, resp = resp if isinstance(resp, tuple) else (None, resp)
        assert resp["ok"] is True, f"登录失败 {username}: {resp}"
        self.user = resp["user"]

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        if method not in ("GET", "HEAD", "OPTIONS"):
            # CSRF 守卫：带会话 Cookie 的写请求必须带此头（模拟前端行为）
            req.add_header("x-knowhow-csrf", "1")
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def get(self, path):    return self._request("GET", path)
    def post(self, path, body=None): return self._request("POST", path, body)
    def put(self, path, body=None):  return self._request("PUT", path, body)
    def delete(self, path):  return self._request("DELETE", path)

    @staticmethod
    def _try_login(username, password):
        """不抛断言的裸登录尝试，返回 (code, body)。"""
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor())
        req = urllib.request.Request(
            f"{BASE}/auth/login",
            data=json.dumps({"username": username,
                             "password": password}).encode(),
            method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")


@pytest.fixture(scope="module")
def admin():
    return ApiClient("admin", "admin")


@pytest.fixture(scope="module")
def editor():
    return ApiClient("editor", "editor")


class TestHealthAndAuth:
    def test_health(self):
        with urllib.request.urlopen(f"{BASE}/health", timeout=5) as r:
            assert r.status == 200

    def test_me_returns_permissions(self, admin):
        code, data = admin.get("/auth/me")
        assert code == 200
        assert data["username"] == "admin"
        assert "role.manage" in data["permissions"]
        assert "account.manage" in data["permissions"]

    def test_editor_has_limited_permissions(self, editor):
        code, data = editor.get("/auth/me")
        assert code == 200
        assert "role.manage" not in data["permissions"]


class TestPermissionIsolation:
    def test_editor_cannot_list_accounts(self, editor):
        code, _ = editor.get("/accounts")
        assert code in (401, 403)

    def test_editor_cannot_list_roles(self, editor):
        code, _ = editor.get("/roles")
        assert code in (401, 403)

    def test_editor_can_view_own_me(self, editor):
        code, data = editor.get("/auth/me")
        assert code == 200 and data["username"] == "editor"

    def test_admin_can_list_accounts(self, admin):
        code, data = admin.get("/accounts")
        assert code == 200
        assert any(a["username"] == "admin" for a in data["accounts"])


class TestAdminProtection:
    def test_builtin_admin_cannot_be_disabled(self, admin):
        code, data = admin.get("/accounts")
        assert code == 200
        admin_id = next(a["id"] for a in data["accounts"]
                        if a["username"] == "admin")
        code, resp = admin.put(f"/accounts/{admin_id}/enabled",
                               {"enabled": False})
        assert code == 400
        assert "不可" in str(resp) or "保护" in str(resp)

    def test_editor_can_be_disabled_and_reenabled(self, admin, editor):
        code, data = admin.get("/accounts")
        editor_id = next(a["id"] for a in data["accounts"]
                         if a["username"] == "editor")
        try:
            code, _ = admin.put(f"/accounts/{editor_id}/enabled",
                                {"enabled": False})
            assert code == 200
            # 停用后 editor 会话被注销
            code, _ = editor.get("/auth/me")
            assert code in (401, 403)
            # 停用账户无法再登录
            code, resp = ApiClient._try_login("editor", "editor")
            assert code == 403
        finally:
            # 无论断言成败都恢复 editor，避免污染后续用例
            admin.put(f"/accounts/{editor_id}/enabled", {"enabled": True})


class TestRoleManagement:
    def test_role_crud_and_rename(self, admin):
        # 创建角色
        code, resp = admin.post("/roles", {
            "code": "pytest_tech", "name": "pytest技术",
            "permissions": ["faq.view", "faq.edit"]})
        assert code == 200 and resp["ok"] is True
        role_id = resp["role"]["id"]
        try:
            # 派生角色：新建"技术1"角色并在基础权限上追加审核
            code, resp2 = admin.post("/roles", {
                "code": "pytest_tech1", "name": "pytest技术1",
                "permissions": ["faq.view", "faq.edit"]})
            assert code == 200 and resp2["ok"] is True
            derived_id = resp2["role"]["id"]
            try:
                # 追加审核权限（对应"技术 + 多一项审核"的派生需求）
                code, _ = admin.put(
                    f"/roles/{derived_id}/permissions",
                    {"permissions": ["faq.view", "faq.edit", "faq.review"]})
                assert code == 200
                # 重命名（技术1 → 技术经理）
                code, resp3 = admin.put(
                    f"/roles/{derived_id}", {"name": "pytest技术经理"})
                assert code == 200
                assert resp3["role"]["name"] == "pytest技术经理"
            finally:
                admin.delete(f"/roles/{derived_id}")
        finally:
            code, resp = admin.delete(f"/roles/{role_id}")
            assert code == 200
