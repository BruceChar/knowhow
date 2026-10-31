"""First-run setup, login and session endpoints."""
from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from knowhow.access import ROLE_TEMPLATES
from knowhow.api.deps import SESSION_COOKIE, current_account
from knowhow.security import (
    create_session,
    hash_password,
    revoke_session,
    token_hash,
    verify_password,
)
from knowhow.settings import settings

router = APIRouter(tags=["auth"])


class SetupBody(BaseModel):
    company_name: str = Field(min_length=1, max_length=120)
    industry: str = Field(default="", max_length=120)
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)


class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


def _setup_cookie(response: Response, account_id: int) -> None:
    token = create_session(account_id)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=settings.cookie_secure, max_age=settings.session_hours * 3600,
    )


@router.get("/api/bootstrap")
def bootstrap():
    with psycopg.connect(
        settings.database_url, row_factory=dict_row,
    ) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM account")
        setup_required = cur.fetchone()["count"] == 0
    return {"setup_required": setup_required, "version": "0.2.0"}


@router.post("/api/setup")
def setup(body: SetupBody, response: Response):
    with psycopg.connect(
        settings.database_url, row_factory=dict_row,
    ) as conn, conn.cursor() as cur:
        cur.execute("LOCK TABLE account IN EXCLUSIVE MODE")
        cur.execute("SELECT count(*) AS count FROM account")
        if cur.fetchone()["count"]:
            raise HTTPException(409, "系统已经完成初始化")
        cur.execute(
            """
            INSERT INTO organization(name,industry,default_language)
            VALUES (%s,%s,'zh-CN') RETURNING id,name,industry
            """,
            (body.company_name.strip(), body.industry.strip()),
        )
        organization = cur.fetchone()
        role_ids = {}
        for code, template in ROLE_TEMPLATES.items():
            cur.execute(
                """
                INSERT INTO access_role
                  (organization_id,code,name,builtin,permissions)
                VALUES (%s,%s,%s,true,%s)
                RETURNING id
                """,
                (
                    organization["id"], code, template["name"],
                    template["permissions"],
                ),
            )
            role_ids[code] = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO account
              (organization_id,username,display_name,password_hash,role,role_id)
            VALUES (%s,%s,%s,%s,'owner',%s)
            RETURNING id,username,display_name,role,role_id
            """,
            (
                organization["id"], body.username.strip(),
                body.display_name.strip(), hash_password(body.password),
                role_ids["owner"],
            ),
        )
        account = cur.fetchone()
        cur.execute(
            "INSERT INTO team(organization_id,name) VALUES (%s,'销售部') RETURNING id",
            (organization["id"],),
        )
        team_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO team_member(team_id,account_id) VALUES (%s,%s)",
            (team_id, account["id"]),
        )
        cur.executemany(
            """
            INSERT INTO answer_rule
              (organization_id,name,rule,rule_kind,status,priority)
            VALUES (%s,%s,%s,%s,'active',%s)
            """,
            [
                (
                    organization["id"], "客户语言一致",
                    "客户使用中文时优先输出中文；没有中文依据时转人工。",
                    "language", 10,
                ),
                (
                    organization["id"], "不编造业务事实",
                    "价格、交期、认证和赔付承诺只能来自已发布知识或人工确认。",
                    "risk", 20,
                ),
                (
                    organization["id"], "先确认关键信息",
                    "产品、数量或目的地缺失且影响结论时，先向客户确认。",
                    "instruction", 30,
                ),
            ],
        )
        conn.commit()
    _setup_cookie(response, account["id"])
    return {
        "ok": True,
        "user": {
            **account,
            "organization_id": organization["id"],
            "organization_name": organization["name"],
            "default_language": "zh-CN",
            "role_name": ROLE_TEMPLATES["owner"]["name"],
            "permissions": ROLE_TEMPLATES["owner"]["permissions"],
            "team_ids": [team_id],
        },
    }


@router.post("/api/auth/login")
def login(body: LoginBody, response: Response):
    with psycopg.connect(
        settings.database_url, row_factory=dict_row,
    ) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.*,r.id AS role_id,r.code AS role,r.name AS role_name,
                   r.permissions,
                   ARRAY(
                     SELECT tm.team_id FROM team_member tm
                     WHERE tm.account_id=a.id ORDER BY tm.team_id
                   ) AS team_ids,
                   o.name AS organization_name,o.default_language
            FROM account a
            JOIN access_role r ON r.id=a.role_id
            JOIN organization o ON o.id=a.organization_id
            WHERE a.username=%s
            """,
            (body.username.strip(),),
        )
        account = cur.fetchone()
    if not account or not account["enabled"] or not verify_password(
        body.password, account["password_hash"],
    ):
        raise HTTPException(401, "账号或密码错误")
    _setup_cookie(response, account["id"])
    account.pop("password_hash", None)
    return {"ok": True, "user": account}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    revoke_session(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/api/auth/me")
def me(user: dict = Depends(current_account)):
    return {"user": user}


@router.post("/api/auth/password")
def change_password(
    body: PasswordBody,
    request: Request,
    user: dict = Depends(current_account),
):
    if body.current_password == body.new_password:
        raise HTTPException(400, "新密码不能与当前密码相同")
    with psycopg.connect(
        settings.database_url, row_factory=dict_row,
    ) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT password_hash FROM account WHERE id=%s",
            (user["id"],),
        )
        account = cur.fetchone()
        if not account or not verify_password(
            body.current_password, account["password_hash"],
        ):
            raise HTTPException(400, "当前密码错误")
        cur.execute(
            """
            UPDATE account
            SET password_hash=%s, must_change_password=false, updated_at=now()
            WHERE id=%s
            """,
            (hash_password(body.new_password), user["id"]),
        )
        token = request.cookies.get(SESSION_COOKIE, "")
        cur.execute(
            "DELETE FROM session WHERE account_id=%s AND token_hash!=%s",
            (user["id"], token_hash(token)),
        )
        conn.commit()
    return {"ok": True}

