"""Small-team account management."""
from __future__ import annotations

from knowhow.db import connect
from knowhow.security import hash_password


def list_accounts(organization_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, username, display_name, role, enabled,
                   must_change_password, created_at
            FROM account WHERE organization_id=%s ORDER BY id
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
    role: str,
) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO account
              (organization_id,username,display_name,password_hash,role,
               must_change_password)
            VALUES (%s,%s,%s,%s,%s,true)
            RETURNING id,username,display_name,role,enabled
            """,
            (
                organization_id, username.strip(), display_name.strip(),
                hash_password(password), role,
            ),
        )
        row = cur.fetchone()
        conn.commit()
    return row


def set_account_enabled(organization_id: int, account_id: int, enabled: bool) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE account SET enabled=%s, updated_at=now()
            WHERE id=%s AND organization_id=%s AND role!='owner'
            """,
            (enabled, account_id, organization_id),
        )
        if cur.rowcount == 0:
            raise ValueError("不能停用企业负责人或账户不存在")
        cur.execute("DELETE FROM session WHERE account_id=%s", (account_id,))
        conn.commit()

