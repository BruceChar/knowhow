"""V2 route and frontend-asset smoke tests."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from knowhow.main import app

ROOT = Path(__file__).resolve().parent.parent


def test_expected_v2_routes_are_registered():
    paths = set(app.openapi()["paths"])
    for path in (
        "/api/bootstrap",
        "/api/setup",
        "/api/auth/login",
        "/api/auth/password",
        "/api/ask",
        "/api/dashboard",
        "/api/knowledge",
        "/api/imports",
        "/api/imports/preview",
        "/api/gaps",
        "/api/team",
        "/api/teams",
        "/api/roles",
        "/api/training/quiz",
        "/api/runtime/info",
        "/api/teams/all",
        "/api/team-roles",
        "/api/teams/{team_id}/members",
        "/api/teams/{team_id}/apply-role",
        "/api/facts",
        "/api/facts/import",
        "/api/invites",
        "/api/invites/{token}",
    ):
        assert path in paths


def test_frontend_assets_are_independent_and_use_custom_dialogs():
    html = (ROOT / "src/knowhow/web/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "src/knowhow/web/assets/app.js").read_text(encoding="utf-8")
    assert "/assets/app.css" in html
    assert "/assets/app.js" in html
    assert "<style" not in html
    assert re.search(r"\b(alert|confirm|prompt)\s*\(", javascript) is None
    assert "function openDialog" in javascript


def test_index_page_is_served():
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "建立公司的销售知识工作台" in response.text


def test_invitation_join_page_is_served():
    response = TestClient(app).get("/join?token=example")
    assert response.status_code == 200
    assert "团队邀请" in response.text

