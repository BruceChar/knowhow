"""Small-team accounts, membership scopes and team leader duties."""
from __future__ import annotations

import re
import secrets

from knowhow.access import valid_permissions
from knowhow.db import connect
from knowhow.security import hash_password


def generate_role_code(name: str) -> str:
    code = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    code = code[:32] or f"custom_{secrets.token_hex(4)}"
    return f"role_{code}" if code[0].isdigit() else code


def list_accounts(organization_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id,a.username,a.display_name,a.enabled,
                   a.must_change_password,a.created_at,
                   r.id AS role_id,r.code AS role,r.name AS role_name,
                   r.permissions,
                   COALESCE(
                     ARRAY_AGG(t.name ORDER BY t.id)
                       FILTER (WHERE t.id IS NOT NULL),
                     '{}'
                   ) AS team_names,
                   COALESCE(
                     jsonb_agg(jsonb_build_object(
                       'team_id',t.id,'team_name',t.name,
                       'is_leader',tm.is_leader
                     ) ORDER BY t.id)
                       FILTER (WHERE t.id IS NOT NULL),
                     '[]'::jsonb
                   ) AS team_roles
            FROM account a
            JOIN access_role r ON r.id=a.role_id
            LEFT JOIN team_member tm ON tm.account_id=a.id
            LEFT JOIN team t ON t.id=tm.team_id
            WHERE a.organization_id=%s
            GROUP BY a.id,r.id
            ORDER BY a.id
            """,
            (organization_id,),
        )
        return cur.fetchall()


def create_account(
    organization_id: int,
    *,
    username: str,
    display_name: str,
    password: str,
    role_id: int,
    team_ids: list[int] | None = None,
) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT code FROM access_role
            WHERE id=%s AND organization_id=%s AND code!='owner'
              AND team_only=false
            """,
            (role_id, organization_id),
        )
        role = cur.fetchone()
        if not role:
            raise ValueError("账户角色不存在或不能分配")
        cur.execute(
            """
            INSERT INTO account
              (organization_id,username,display_name,password_hash,role,role_id,
               must_change_password)
            VALUES (%s,%s,%s,%s,%s,%s,true)
            RETURNING id,username,display_name,role,role_id,enabled
            """,
            (
                organization_id, username.strip(), display_name.strip(),
                hash_password(password), role["code"], role_id,
            ),
        )
        account = cur.fetchone()
        selected = team_ids or []
        if not selected:
            cur.execute(
                "SELECT id FROM team WHERE organization_id=%s ORDER BY id LIMIT 1",
                (organization_id,),
            )
            first = cur.fetchone()
            selected = [first["id"]] if first else []
        for team_id in set(selected):
            cur.execute(
                """
                INSERT INTO team_member(team_id,account_id,is_leader)
                SELECT t.id,%s,false FROM team t
                WHERE t.id=%s AND t.organization_id=%s
                """,
                (account["id"], team_id, organization_id),
            )
        conn.commit()
    return account


def set_account_enabled(organization_id: int, account_id: int, enabled: bool) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE account a SET enabled=%s, updated_at=now()
            FROM access_role r
            WHERE a.id=%s AND a.organization_id=%s AND a.role_id=r.id
              AND r.code!='owner'
            """,
            (enabled, account_id, organization_id),
        )
        if cur.rowcount == 0:
            raise ValueError("不能停用企业负责人或账户不存在")
        cur.execute("DELETE FROM session WHERE account_id=%s", (account_id,))
        conn.commit()


def list_roles(organization_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id,code,name,builtin,team_only,permissions,created_at
            FROM access_role
            WHERE organization_id=%s AND team_only=false
            ORDER BY builtin DESC,id
            """,
            (organization_id,),
        )
        return cur.fetchall()


def create_role(
    organization_id: int,
    *,
    code: str,
    name: str,
    permissions: list[str],
) -> dict:
    code = (code or "").strip().lower() or generate_role_code(name)
    if code in {"owner", "admin", "steward", "sales", "lead"}:
        raise ValueError("不能覆盖系统保留角色编码")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO access_role
              (organization_id,code,name,builtin,team_only,permissions)
            VALUES (%s,%s,%s,false,false,%s)
            RETURNING id,code,name,builtin,permissions
            """,
            (organization_id, code, name.strip(), valid_permissions(permissions)),
        )
        row = cur.fetchone()
        conn.commit()
    return row


def update_role_permissions(
    organization_id: int,
    role_id: int,
    permissions: list[str],
) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE access_role
            SET permissions=%s,updated_at=now()
            WHERE id=%s AND organization_id=%s
              AND code!='owner' AND team_only=false
            RETURNING id,code,name,builtin,permissions
            """,
            (valid_permissions(permissions), role_id, organization_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("角色不存在或老板角色不可修改")
        conn.commit()
    return row


def list_teams_for_account(organization_id: int, account_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id,t.name,tm.is_leader
            FROM team t
            JOIN team_member tm ON tm.team_id=t.id
            WHERE t.organization_id=%s AND tm.account_id=%s
            ORDER BY t.id
            """,
            (organization_id, account_id),
        )
        return cur.fetchall()


def list_teams(organization_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id,t.name,
                   count(tm.account_id)::int AS member_count,
                   count(tm.account_id)
                     FILTER (WHERE tm.is_leader)::int AS leader_count
            FROM team t
            LEFT JOIN team_member tm ON tm.team_id=t.id
            WHERE t.organization_id=%s
            GROUP BY t.id ORDER BY t.id
            """,
            (organization_id,),
        )
        return cur.fetchall()


def create_team(organization_id: int, name: str) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO team(organization_id,name) VALUES (%s,%s) RETURNING id,name",
            (organization_id, name.strip()),
        )
        row = cur.fetchone()
        conn.commit()
    return row


def set_account_teams(
    organization_id: int,
    account_id: int,
    team_ids: list[int],
) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM account WHERE id=%s AND organization_id=%s",
            (account_id, organization_id),
        )
        if not cur.fetchone():
            raise LookupError("账户不存在")
        cur.execute("DELETE FROM team_member WHERE account_id=%s", (account_id,))
        for team_id in set(team_ids):
            cur.execute(
                """
                INSERT INTO team_member(team_id,account_id,is_leader)
                SELECT id,%s,false FROM team
                WHERE id=%s AND organization_id=%s
                """,
                (account_id, team_id, organization_id),
            )
        cur.execute("DELETE FROM session WHERE account_id=%s", (account_id,))
        conn.commit()


def list_team_members(organization_id: int, team_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id,a.username,a.display_name,tm.is_leader,
                   ar.name AS account_role_name
            FROM team_member tm
            JOIN account a ON a.id=tm.account_id
            JOIN access_role ar ON ar.id=a.role_id
            JOIN team t ON t.id=tm.team_id
            WHERE tm.team_id=%s AND t.organization_id=%s
            ORDER BY tm.is_leader DESC,a.id
            """,
            (team_id, organization_id),
        )
        return cur.fetchall()


def set_team_member_leader(
    organization_id: int,
    team_id: int,
    account_id: int,
    is_leader: bool,
) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE team_member tm SET is_leader=%s
            FROM team t, account a
            WHERE tm.team_id=%s AND tm.account_id=%s
              AND t.id=tm.team_id AND t.organization_id=%s
              AND a.id=tm.account_id AND a.role_id!=(
                SELECT id FROM access_role
                WHERE organization_id=%s AND code='owner'
              )
            RETURNING tm.account_id,tm.team_id,tm.is_leader
            """,
            (
                is_leader, team_id, account_id, organization_id,
                organization_id,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("成员不存在或负责人不能设为组长")
        cur.execute("DELETE FROM session WHERE account_id=%s", (account_id,))
        conn.commit()
    return row


def remove_team_member(
    organization_id: int,
    team_id: int,
    account_id: int,
) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM team_member tm
            USING team t, account a
            WHERE tm.team_id=%s AND tm.account_id=%s
              AND t.id=tm.team_id AND t.organization_id=%s
              AND a.id=tm.account_id AND a.role_id!=(
                SELECT id FROM access_role
                WHERE organization_id=%s AND code='owner'
              )
            """,
            (team_id, account_id, organization_id, organization_id),
        )
        if cur.rowcount == 0:
            raise ValueError("成员不存在或老板不能移出团队")
        cur.execute("DELETE FROM session WHERE account_id=%s", (account_id,))
        conn.commit()

