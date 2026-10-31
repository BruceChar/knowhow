"""Authentication and role dependencies."""
from __future__ import annotations

import psycopg
from fastapi import Depends, HTTPException, Request
from psycopg.rows import dict_row

from knowhow.security import session_account
from knowhow.settings import settings

SESSION_COOKIE = "knowhow_session"


def current_account(request: Request) -> dict:
    account = session_account(request.cookies.get(SESSION_COOKIE, ""))
    if account:
        return account
    if settings.dev_auth:
        with psycopg.connect(
            settings.database_url, row_factory=dict_row,
        ) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id,a.organization_id,a.username,a.display_name,
                       r.id AS role_id,r.code AS role,r.name AS role_name,
                       r.permissions,
                       ARRAY(
                         SELECT tm.team_id FROM team_member tm
                         WHERE tm.account_id=a.id ORDER BY tm.team_id
                       ) AS team_ids,
                       o.name AS organization_name,o.default_language
                FROM account a
                JOIN access_role r ON r.id=a.role_id
                JOIN organization o ON o.id=a.organization_id
                WHERE a.enabled ORDER BY a.id LIMIT 1
                """
            )
            account = cur.fetchone()
        if account:
            return account
    raise HTTPException(401, "请先登录")


def require_roles(*roles: str):
    allowed = set(roles)

    def dependency(user: dict = Depends(current_account)) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(403, "当前角色无权执行此操作")
        return user

    return dependency


def require_permission(code: str):
    def dependency(user: dict = Depends(current_account)) -> dict:
        if code not in (user.get("permissions") or []):
            raise HTTPException(403, f"缺少权限：{code}")
        return user

    return dependency

