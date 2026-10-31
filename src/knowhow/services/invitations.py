"""LAN invitation links and one-time account creation."""
from __future__ import annotations

import secrets
import socket
from datetime import datetime, timedelta, timezone

import segno

from knowhow.db import connect
from knowhow.security import hash_password, token_hash


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        pass
    finally:
        sock.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address.startswith(("10.", "172.", "192.168.")):
                return address
    except Exception:
        pass
    return "127.0.0.1"


def runtime_info(port: int) -> dict:
    ip = lan_ip()
    base_url = f"http://{ip}:{port}"
    qr = segno.make(base_url, error="m")
    return {
        "ip": ip,
        "base_url": base_url,
        "qr_svg": qr.svg_inline(scale=4, border=2),
    }


def create_invite(
    organization_id: int,
    created_by: int,
    *,
    role_id: int,
    team_id: int,
    base_url: str,
    expires_hours: int = 72,
) -> dict:
    raw_token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.name AS role_name,t.name AS team_name,
                   o.name AS organization_name
            FROM access_role r
            JOIN team t ON t.id=%s AND t.organization_id=%s
            JOIN organization o ON o.id=%s
            WHERE r.id=%s AND r.organization_id=%s AND r.code!='owner'
              AND r.team_only=false
            """,
            (
                team_id, organization_id, organization_id,
                role_id, organization_id,
            ),
        )
        details = cur.fetchone()
        if not details:
            raise ValueError("角色或团队不存在")
        cur.execute(
            """
            INSERT INTO account_invite
              (organization_id,token_hash,role_id,team_id,created_by,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                organization_id, token_hash(raw_token), role_id, team_id,
                created_by, expires,
            ),
        )
        invite_id = cur.fetchone()["id"]
        conn.commit()
    return {
        "id": invite_id,
        "url": f"{base_url.rstrip('/')}/join?token={raw_token}",
        "expires_at": expires,
        **details,
    }


def inspect_invite(raw_token: str) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT i.id,i.expires_at,i.used_at,o.name AS organization_name,
                   r.name AS role_name,t.name AS team_name
            FROM account_invite i
            JOIN organization o ON o.id=i.organization_id
            JOIN access_role r ON r.id=i.role_id
            JOIN team t ON t.id=i.team_id
            WHERE i.token_hash=%s
            """,
            (token_hash(raw_token),),
        )
        invite = cur.fetchone()
    if not invite:
        raise LookupError("邀请链接不存在")
    if invite["used_at"]:
        raise ValueError("邀请链接已使用")
    if invite["expires_at"] <= datetime.now(timezone.utc):
        raise ValueError("邀请链接已过期")
    return invite


def accept_invite(
    raw_token: str,
    *,
    username: str,
    display_name: str,
    password: str,
) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT i.*,r.code AS role_code,r.name AS role_name,t.name AS team_name,
                   o.name AS organization_name
            FROM account_invite i
            JOIN access_role r ON r.id=i.role_id
            JOIN team t ON t.id=i.team_id
            JOIN organization o ON o.id=i.organization_id
            WHERE i.token_hash=%s
            FOR UPDATE OF i
            """,
            (token_hash(raw_token),),
        )
        invite = cur.fetchone()
        if not invite:
            raise LookupError("邀请链接不存在")
        if invite["used_at"]:
            raise ValueError("邀请链接已使用")
        if invite["expires_at"] <= datetime.now(timezone.utc):
            raise ValueError("邀请链接已过期")
        try:
            cur.execute(
                """
                INSERT INTO account
                  (organization_id,username,display_name,password_hash,role,
                   role_id,must_change_password)
                VALUES (%s,%s,%s,%s,%s,%s,false)
                RETURNING id,username,display_name,role,role_id
                """,
                (
                    invite["organization_id"], username.strip(),
                    display_name.strip(), hash_password(password),
                    invite["role_code"], invite["role_id"],
                ),
            )
        except Exception as exc:
            if "duplicate key" in str(exc).lower():
                raise ValueError("登录账号已存在") from exc
            raise
        account = cur.fetchone()
        cur.execute(
            """
            INSERT INTO team_member(team_id,account_id,is_leader)
            VALUES (%s,%s,false)
            """,
            (invite["team_id"], account["id"]),
        )
        cur.execute(
            """
            UPDATE account_invite SET used_at=now(),used_by=%s WHERE id=%s
            """,
            (account["id"], invite["id"]),
        )
        conn.commit()
    return {
        **account,
        "organization_id": invite["organization_id"],
        "organization_name": invite["organization_name"],
        "role_name": invite["role_name"],
        "team_name": invite["team_name"],
    }

