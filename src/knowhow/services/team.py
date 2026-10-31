"""Small-team account management."""
from __future__ import annotations

from knowhow.db import connect
from knowhow.security import hash_password
from knowhow.access import valid_permissions


def list_accounts(organization_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, a.username, a.display_name, a.enabled,
                   a.must_change_password, a.created_at,
                   r.id AS role_id, r.code AS role, r.name AS role_name,
                   r.permissions,
                   COALESCE(
                     ARRAY_AGG(t.name ORDER BY t.id)
                       FILTER (WHERE t.id IS NOT NULL),
                     '{}'
                   ) AS team_names
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
            """,
            (role_id, organization_id),
        )
        role = cur.fetchone()
        if not role:
            raise ValueError("角色不存在或不能分配老板角色")
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
        row = cur.fetchone()
        selected_teams = team_ids or []
        if selected_teams:
            cur.execute(
                """
                INSERT INTO team_member(team_id,account_id)
                SELECT id,%s FROM team
                WHERE organization_id=%s AND id=ANY(%s)
                """,
                (row["id"], organization_id, selected_teams),
            )
        else:
            cur.execute(
                """
                INSERT INTO team_member(team_id,account_id)
                SELECT id,%s FROM team
                WHERE organization_id=%s ORDER BY id LIMIT 1
                ON CONFLICT DO NOTHING
                """,
                (row["id"], organization_id),
            )
        conn.commit()
    return row


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
            SELECT id, code, name, builtin, permissions, created_at
            FROM access_role WHERE organization_id=%s ORDER BY builtin DESC, id
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
    if code in {"owner", "admin", "steward", "sales"}:
        raise ValueError("不能覆盖系统内置角色编码")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO access_role
              (organization_id,code,name,builtin,permissions)
            VALUES (%s,%s,%s,false,%s)
            RETURNING id,code,name,builtin,permissions
            """,
            (organization_id, code.strip(), name.strip(), valid_permissions(permissions)),
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
            SET permissions=%s, updated_at=now()
            WHERE id=%s AND organization_id=%s AND code!='owner'
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
            SELECT t.id,t.name
            FROM team t JOIN team_member tm ON tm.team_id=t.id
            WHERE t.organization_id=%s AND tm.account_id=%s
            ORDER BY t.id
            """,
            (organization_id, account_id),
        )
        return cur.fetchall()


def create_team(organization_id: int, name: str) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team(organization_id,name) VALUES (%s,%s)
            RETURNING id,name
            """,
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
        cur.execute(
            "DELETE FROM team_member WHERE account_id=%s",
            (account_id,),
        )
        if team_ids:
            cur.execute(
                """
                INSERT INTO team_member(team_id,account_id)
                SELECT id,%s FROM team
                WHERE organization_id=%s AND id=ANY(%s)
                """,
                (account_id, organization_id, team_ids),
            )
        cur.execute("DELETE FROM session WHERE account_id=%s", (account_id,))
        conn.commit()

