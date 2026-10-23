"""账户登录认证：密码哈希（pbkdf2）+ 会话 token（DB sessions 表）。

设计：
- 密码：标准库 hashlib.pbkdf2_hmac（无第三方依赖），salt 随机 16B，迭代 260k
- 会话：secrets.token_urlsafe(32)，存 sessions 表，24h 过期，滑动续期
- 与 RBAC 集成：get_current_user 支持 Authorization: Bearer <token>，
  未带 token 时回退 X-User-Id（开发/联调兼容，生产应关闭）
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg

DEFAULT_DSN = "postgresql://rag:rag_dev_2026@localhost:5433/rag"
SESSION_TTL_HOURS = 24
_PBKDF2_ITER = 260_000


# ---------------- 密码哈希 ----------------

def hash_password(password: str) -> str:
    """返回 'pbkdf2$iterations$salt_hex$hash_hex'。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
    return f"pbkdf2${_PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ---------------- 种子用户（演示默认密码，生产必须修改） ----------------

DEFAULT_USERS = [
    ("u_admin", "管理员", "admin", "admin123"),
    ("u_reviewer", "审核员", "reviewer", "reviewer123"),
    ("u_editor", "业务员", "editor", "editor123"),
    ("u_agent", "客服", "agent", "agent123"),
]


def seed_users(dsn: str = DEFAULT_DSN, force: bool = False) -> int:
    """幂等写入种子用户；force=True 时重置密码为默认值。"""
    created = 0
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for uid, name, role, pw in DEFAULT_USERS:
            cur.execute("SELECT count(*) FROM users WHERE id=%s", (uid,))
            exists = cur.fetchone()[0] > 0
            if not exists or force:
                cur.execute("""
                    INSERT INTO users (id, name, role, password_hash)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE
                      SET name=EXCLUDED.name, role=EXCLUDED.role,
                          password_hash=EXCLUDED.password_hash
                """, (uid, name, role, hash_password(pw)))
                created += 1
        conn.commit()
    return created


# ---------------- 会话 ----------------

def create_session(user_id: str, dsn: str = DEFAULT_DSN) -> str:
    token = secrets.token_urlsafe(32)
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sessions (token, user_id, expires_at)
            VALUES (%s,%s,%s)
        """, (token, user_id, exp))
        conn.commit()
    return token


def get_session_user(token: str, dsn: str = DEFAULT_DSN) -> Optional[dict]:
    """按 token 查有效会话 → 用户；同时滑动续期 last_seen。"""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT u.id, u.name, u.role
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token=%s AND s.expires_at > now()
        """, (token,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE sessions SET last_seen=now() WHERE token=%s", (token,))
            conn.commit()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "role": row[2]}


def delete_session(token: str, dsn: str = DEFAULT_DSN) -> bool:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token=%s", (token,))
        conn.commit()
        return cur.rowcount > 0


def authenticate(username: str, password: str, dsn: str = DEFAULT_DSN) -> Optional[dict]:
    """校验用户名密码，成功返回 {id,name,role,token}。"""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, role, password_hash FROM users WHERE id=%s",
                    (username,))
        row = cur.fetchone()
    if not row or not verify_password(password, row[3]):
        return None
    token = create_session(row[0], dsn)
    return {"id": row[0], "name": row[1], "role": row[2], "token": token}
