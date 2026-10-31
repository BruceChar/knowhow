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


# ---------------- 默认账户（system_account） ----------------
# 系统默认只有两个账户：admin/admin（管理员）、editor/editor（业务员）。
# 其余人员由 admin 在"账户管理"中创建并绑定角色。

DEFAULT_ACCOUNTS = [
    ("admin", "管理员", "admin", "admin"),      # 绑定内置角色 admin
    ("editor", "业务员", "editor", "editor"),   # 绑定内置角色 editor
]

_ACCOUNT_ROLE_CODE = {
    "admin": "admin",
    "editor": "editor",
}


def seed_users(dsn: str = DEFAULT_DSN, force: bool = False) -> int:
    """幂等写入默认账户（system_account）+ 角色绑定；force=True 时重置密码。

    密码策略（生产）：
    - PROD=1 且未注入 SEED_PASSWORDS → 随机生成密码并打印一次
    - 注入 SEED_PASSWORDS（格式 admin=xxx,editor=xxx）→ 使用注入的强密码
    - 开发模式 → 演示默认密码（admin/admin、editor/editor，仅限本地）
    """
    created = 0
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM system_role WHERE code='admin'")
        admin_role_id = cur.fetchone()[0]
        for username, display, role_code, dev_pw in DEFAULT_ACCOUNTS:
            cur.execute("SELECT id FROM system_account WHERE username=%s", (username,))
            row = cur.fetchone()
            exists = row is not None
            if not exists or force:
                if username in SEED_PASSWORDS:
                    pw = SEED_PASSWORDS[username]
                elif PROD:
                    pw = secrets.token_urlsafe(12)
                    print(f"[seed] 生成随机密码 {username}={pw}（仅本次打印，请立即保存）")
                else:
                    pw = dev_pw
                if exists:
                    account_id = row[0]
                    cur.execute("""
                        UPDATE system_account
                        SET password_hash=%s, must_change_password=%s
                        WHERE id=%s
                    """, (hash_password(pw), force or PROD and username not in SEED_PASSWORDS,
                          account_id))
                else:
                    cur.execute("""
                        INSERT INTO system_account (username, display_name, password_hash,
                                                    must_change_password)
                        VALUES (%s,%s,%s,%s) RETURNING id
                    """, (username, display, hash_password(pw),
                          force or PROD and username not in SEED_PASSWORDS))
                    account_id = cur.fetchone()[0]
                cur.execute("""
                    INSERT INTO system_account_role (account_id, role_id)
                    SELECT %s, id FROM system_role WHERE code=%s
                    ON CONFLICT DO NOTHING
                """, (account_id, _ACCOUNT_ROLE_CODE.get(username, role_code)))
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


def create_session(account_id: int, dsn: str = DEFAULT_DSN) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = _session_fingerprint(token)
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sessions (token, account_id, expires_at)
            VALUES (%s,%s,%s)
        """, (token_hash, account_id, exp))
        conn.commit()
    return token


def get_session_user(token: str, dsn: str = DEFAULT_DSN) -> Optional[dict]:
    """按 token 查有效会话 → 账户（含多角色与权限集合）；滑动续期 last_seen。"""
    token_hash = _session_fingerprint(token)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.id, a.username, a.display_name, a.enabled
            FROM sessions s JOIN system_account a ON a.id = s.account_id
            WHERE s.token=%s AND s.expires_at > now()
        """, (token_hash,))
        row = cur.fetchone()
        if not row or not row[3]:
            return None
        # 角色与权限集合
        cur.execute("""
            SELECT r.code FROM system_role r
            JOIN system_account_role ar ON ar.role_id = r.id
            WHERE ar.account_id=%s
        """, (row[0],))
        roles = [x[0] for x in cur.fetchall()]
        cur.execute("""
            SELECT bool_or(r.data_scope='full')
            FROM system_role r
            JOIN system_account_role ar ON ar.role_id = r.id
            WHERE ar.account_id=%s
        """, (row[0],))
        has_full = cur.fetchone()[0] or False
        cur.execute("""
            SELECT DISTINCT rp.permission_code
            FROM system_role_permission rp
            JOIN system_role r ON r.id = rp.role_id
            JOIN system_account_role ar ON ar.role_id = r.id
            WHERE ar.account_id=%s
        """, (row[0],))
        permissions = {x[0] for x in cur.fetchall()}
        cur.execute("UPDATE sessions SET last_seen=now() WHERE token=%s", (token_hash,))
        conn.commit()
    return {"id": row[0], "username": row[1], "name": row[2],
            "roles": roles, "permissions": permissions,
            "data_scope": ("full" if has_full else "standard")}


def delete_session(token: str, dsn: str = DEFAULT_DSN) -> bool:
    token_hash = _session_fingerprint(token)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token=%s", (token_hash,))
        conn.commit()
        return cur.rowcount > 0


def authenticate(username: str, password: str, dsn: str = DEFAULT_DSN,
                 ip: str = "") -> Optional[dict]:
    """校验用户名密码，成功返回 {id,name,roles,token,must_change}。

    失败时记录限流计数；锁定期间直接拒绝。
    """
    if is_locked(username, ip):
        return {"locked": True}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, display_name, password_hash, must_change_password, enabled
            FROM system_account WHERE username=%s
        """, (username,))
        row = cur.fetchone()
        cur.execute("""
            SELECT r.code FROM system_role r
            JOIN system_account_role ar ON ar.role_id = r.id
            WHERE ar.account_id=%s
        """, (row[0],)) if row else None
        roles = [x[0] for x in cur.fetchall()] if row else []
    if not row or not verify_password(password, row[2]):
        n = _record_failure(username, ip)
        return None if n < LOGIN_MAX_ATTEMPTS else {"locked": True}
    if not row[4]:
        return {"disabled": True}
    _clear_failures(username, ip)
    token = create_session(row[0], dsn)
    return {"id": row[0], "username": username, "name": row[1],
            "roles": roles, "token": token, "must_change": row[3]}


def set_password(account_id: int, new_password: str,
                 dsn: str = DEFAULT_DSN) -> None:
    """修改密码并清除强制改密标志；返回前调用方应注销该账户其他会话。"""
    if len(new_password) < 8:
        raise ValueError("密码长度至少 8 位")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE system_account SET password_hash=%s, must_change_password=false
            WHERE id=%s
        """, (hash_password(new_password), account_id))
        conn.commit()


def revoke_sessions(account_id: int, keep_token: str | None = None,
                    dsn: str = DEFAULT_DSN) -> None:
    """注销账户全部会话（可保留当前 token，如改密后保持本会话）。"""
    keep_hash = _session_fingerprint(keep_token) if keep_token else None
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if keep_hash:
            cur.execute("DELETE FROM sessions WHERE account_id=%s AND token<>%s",
                        (account_id, keep_hash))
        else:
            cur.execute("DELETE FROM sessions WHERE account_id=%s", (account_id,))
        conn.commit()
