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

from src.config import RAG_DSN as DEFAULT_DSN, SESSION_TTL_HOURS, PROD, SEED_PASSWORDS
from src.config import LOGIN_MAX_ATTEMPTS, LOGIN_LOCK_MINUTES

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
    """幂等写入种子用户；force=True 时重置密码。

    密码策略（生产）：
    - PROD=1 且未注入 SEED_PASSWORDS → 随机生成密码并打印一次
    - 注入 SEED_PASSWORDS → 使用注入的强密码
    - 开发模式 → 演示默认密码（文档已注明仅限本地）
    """
    created = 0
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for uid, name, role, dev_pw in DEFAULT_USERS:
            cur.execute("SELECT count(*) FROM users WHERE id=%s", (uid,))
            exists = cur.fetchone()[0] > 0
            if not exists or force:
                if uid in SEED_PASSWORDS:
                    pw = SEED_PASSWORDS[uid]
                elif PROD:
                    pw = secrets.token_urlsafe(12)
                    print(f"[seed] 生成随机密码 {uid}={pw}（仅本次打印，请立即保存）")
                else:
                    pw = dev_pw
                cur.execute("""
                    INSERT INTO users (id, name, role, password_hash, must_change_password)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE
                      SET name=EXCLUDED.name, role=EXCLUDED.role,
                          password_hash=EXCLUDED.password_hash,
                          must_change_password=EXCLUDED.must_change_password
                """, (uid, name, role, hash_password(pw), force or PROD and uid not in SEED_PASSWORDS))
                created += 1
        conn.commit()
    return created


# ---------------- 登录限流（进程内；单实例部署足够） ----------------

_login_failures: dict[str, list] = {}  # key → [失败次数, 首次失败时间]


def _rate_key(username: str, ip: str) -> str:
    return f"{username}@{ip}"


def is_locked(username: str, ip: str) -> bool:
    """是否处于锁定态（连续失败超限）。"""
    key = _rate_key(username, ip)
    rec = _login_failures.get(key)
    if not rec:
        return False
    n, first = rec
    if n >= LOGIN_MAX_ATTEMPTS:
        if datetime.now(timezone.utc).timestamp() - first < LOGIN_LOCK_MINUTES * 60:
            return True
        _login_failures.pop(key, None)  # 锁定到期自动解锁
    return False


def _record_failure(username: str, ip: str) -> int:
    """记录一次失败，返回当前连续失败次数。"""
    key = _rate_key(username, ip)
    now = datetime.now(timezone.utc).timestamp()
    rec = _login_failures.get(key)
    if rec and now - rec[1] > LOGIN_LOCK_MINUTES * 60:
        _login_failures[key] = [1, now]  # 窗口重置
        return 1
    _login_failures[key] = [rec[0] + 1, rec[1]] if rec else [1, now]
    return _login_failures[key][0]


def _clear_failures(username: str, ip: str) -> None:
    _login_failures.pop(_rate_key(username, ip), None)


# ---------------- 会话 ----------------

def _session_fingerprint(token: str) -> str:
    """Persist only a one-way fingerprint of a bearer session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: str, dsn: str = DEFAULT_DSN) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = _session_fingerprint(token)
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sessions (token, user_id, expires_at)
            VALUES (%s,%s,%s)
        """, (token_hash, user_id, exp))
        conn.commit()
    return token


def get_session_user(token: str, dsn: str = DEFAULT_DSN) -> Optional[dict]:
    """按 token 查有效会话 → 用户；同时滑动续期 last_seen。"""
    token_hash = _session_fingerprint(token)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT u.id, u.name, u.role
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token=%s AND s.expires_at > now()
        """, (token_hash,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE sessions SET last_seen=now() WHERE token=%s", (token_hash,))
            conn.commit()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "role": row[2]}


def delete_session(token: str, dsn: str = DEFAULT_DSN) -> bool:
    token_hash = _session_fingerprint(token)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token=%s", (token_hash,))
        conn.commit()
        return cur.rowcount > 0


def authenticate(username: str, password: str, dsn: str = DEFAULT_DSN,
                 ip: str = "") -> Optional[dict]:
    """校验用户名密码，成功返回 {id,name,role,token,must_change}。

    失败时记录限流计数；锁定期间直接拒绝。
    """
    if is_locked(username, ip):
        return {"locked": True}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, role, password_hash, must_change_password
            FROM users WHERE id=%s
        """, (username,))
        row = cur.fetchone()
    if not row or not verify_password(password, row[3]):
        n = _record_failure(username, ip)
        return None if n < LOGIN_MAX_ATTEMPTS else {"locked": True}
    _clear_failures(username, ip)
    token = create_session(row[0], dsn)
    return {"id": row[0], "name": row[1], "role": row[2], "token": token,
            "must_change": row[4]}


def set_password(user_id: str, new_password: str, dsn: str = DEFAULT_DSN) -> None:
    """修改密码并清除强制改密标志；返回前调用方应注销该用户其他会话。"""
    if len(new_password) < 8:
        raise ValueError("密码长度至少 8 位")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE users SET password_hash=%s, must_change_password=false
            WHERE id=%s
        """, (hash_password(new_password), user_id))
        conn.commit()


def revoke_sessions(user_id: str, keep_token: str | None = None,
                    dsn: str = DEFAULT_DSN) -> None:
    """注销用户全部会话（可保留当前 token，如改密后保持本会话）。"""
    keep_hash = _session_fingerprint(keep_token) if keep_token else None
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if keep_hash:
            cur.execute("DELETE FROM sessions WHERE user_id=%s AND token<>%s",
                        (user_id, keep_hash))
        else:
            cur.execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))
        conn.commit()
