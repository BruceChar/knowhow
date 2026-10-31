"""RBAC v4：账户 → 角色 → 权限点（system_ 表）。

模型：
  system_account  账户（username 登录）
  system_role     角色（可自建；builtin 内置模板不可删）
  system_permission 权限点（模块 → 页面 → 功能树，静态预置）
  system_role_permission / system_account_role 关联

校验：
  require_permission("faq.edit")  路由级：账户权限集合含该点
  数据级：角色 data_scope（'standard'=public+internal，'full'=全部），
          账户取所绑定角色的最大可见域（并集）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import psycopg
from fastapi import Depends, HTTPException, Request

from src.config import RAG_DSN

DATA_SCOPE_FULL = "full"
DATA_SCOPE_STANDARD = "standard"


@dataclass
class User:
    """当前登录账户：多角色 + 权限点集合。"""
    id: int
    username: str
    name: str
    roles: list[str] = field(default_factory=list)
    permissions: set[str] = field(default_factory=set)
    data_scope: str = DATA_SCOPE_STANDARD

    def has(self, code: str) -> bool:
        return code in self.permissions

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles


def get_current_user(request: Request) -> User:
    """解析当前账户：
    1) Cookie kh_session —— 生产会话
    2) Authorization: Bearer <token> —— API/脚本
    3) X-User-Id —— 仅 KNOWHOW_DEV_AUTH=1（本地开发）
    """
    from src.auth import auth as _auth
    token = request.cookies.get("kh_session") or ""
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    if token:
        info = _auth.get_session_user(token)
        if info is None:
            raise HTTPException(status_code=401, detail="会话无效或已过期，请重新登录")
        return _to_user(info)
    import os
    if os.environ.get("KNOWHOW_DEV_AUTH", "0") == "1":
        username = request.headers.get("X-User-Id", "admin")
        with psycopg.connect(RAG_DSN) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT a.id, a.username, a.display_name,
                       coalesce(bool_or(r.data_scope='full'), false)
                FROM system_account a
                LEFT JOIN system_account_role ar ON ar.account_id = a.id
                LEFT JOIN system_role r ON r.id = ar.role_id
                WHERE a.username=%s AND a.enabled GROUP BY a.id
            """, (username,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="未知账户")
        info = {"id": row[0], "username": row[1], "name": row[2],
                "roles": [], "permissions": set(),
                "data_scope": DATA_SCOPE_FULL if row[3] else DATA_SCOPE_STANDARD}
        return _to_user(info)
    raise HTTPException(status_code=401, detail="请先登录")


def _to_user(info: dict) -> User:
    return User(
        id=info["id"], username=info.get("username", ""), name=info["name"],
        roles=info.get("roles", []), permissions=info.get("permissions", set()),
        data_scope=info.get("data_scope", DATA_SCOPE_STANDARD),
    )


def require_permission(code: str) -> Callable:
    """路由级 RBAC：账户权限集合不含该点 → 403。"""
    def dep(user: User = Depends(get_current_user)) -> User:
        if not user.has(code):
            raise HTTPException(status_code=403, detail=f"缺少权限：{code}")
        return user
    return dep


# 向后兼容：旧 require_role → 映射到权限点
def require_role(min_role: str) -> Callable:
    """兼容旧调用：按角色级别放行（新代码请用 require_permission）。"""
    level = {"agent": 1, "editor": 2, "reviewer": 3, "admin": 4}
    need = level.get(min_role, 4)
    role_rank = {"agent": 1, "editor": 2, "reviewer": 3, "admin": 4}

    def dep(user: User = Depends(get_current_user)) -> User:
        top = max((role_rank.get(r, 0) for r in user.roles), default=0)
        if top < need:
            raise HTTPException(status_code=403,
                                detail=f"需要 {min_role} 及以上权限")
        return user
    return dep


def data_permission_clause(user: User, alias: str = "") -> str:
    """数据级 SQL 过滤：按账户最大可见域返回 permission IN (...) 子句。"""
    if user.data_scope == DATA_SCOPE_FULL:
        visible = ["public", "internal", "confidential"]
    else:
        visible = ["public", "internal"]
    quoted = ", ".join(f"'{p}'" for p in visible)
    prefix = f"{alias}." if alias else ""
    return f"{prefix}permission IN ({quoted})"


# ================= 账户管理服务 =================

def list_accounts(dsn: str = RAG_DSN) -> list[dict]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.id, a.username, a.display_name, a.enabled,
                   a.must_change_password, a.created_at,
                   coalesce(string_agg(r.name, '、' ORDER BY r.id), ''),
                   coalesce(bool_or(r.data_scope='full'), false)
            FROM system_account a
            LEFT JOIN system_account_role ar ON ar.account_id = a.id
            LEFT JOIN system_role r ON r.id = ar.role_id
            GROUP BY a.id ORDER BY a.id
        """)
        return [{
            "id": row[0], "username": row[1], "name": row[2],
            "enabled": row[3], "must_change": row[4],
            "created_at": row[5].isoformat(),
            "roles": row[6].split("、") if row[6] else [],
            "data_scope": DATA_SCOPE_FULL if row[7] else DATA_SCOPE_STANDARD,
        } for row in cur.fetchall()]


def create_account(username: str, display_name: str, role_codes: list[str],
                   initial_password: str | None = None,
                   dsn: str = RAG_DSN) -> tuple[dict, str | None]:
    """创建账户并绑定角色；返回 (账户, 临时密码|None)。"""
    from src.auth.auth import hash_password
    import secrets
    temp_password = initial_password or secrets.token_urlsafe(12)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM system_account WHERE username=%s", (username,))
        if cur.fetchone():
            raise ValueError(f"账户已存在: {username}")
        cur.execute("""
            INSERT INTO system_account (username, display_name, password_hash,
                                        must_change_password)
            VALUES (%s,%s,%s,true) RETURNING id
        """, (username, display_name, hash_password(temp_password)))
        aid = cur.fetchone()[0]
        for code in role_codes:
            cur.execute("""
                INSERT INTO system_account_role (account_id, role_id)
                SELECT %s, id FROM system_role WHERE code=%s
                ON CONFLICT DO NOTHING
            """, (aid, code))
        conn.commit()
    acct = list_accounts(dsn)
    acct = next(a for a in acct if a["id"] == aid)
    return acct, (None if initial_password else temp_password)


def update_account_roles(account_id: int, role_codes: list[str],
                         dsn: str = RAG_DSN) -> None:
    """重绑角色（替换式）。"""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM system_account_role WHERE account_id=%s",
                    (account_id,))
        for code in role_codes:
            cur.execute("""
                INSERT INTO system_account_role (account_id, role_id)
                SELECT %s, id FROM system_role WHERE code=%s
                ON CONFLICT DO NOTHING
            """, (account_id, code))
        conn.commit()


def set_account_enabled(account_id: int, enabled: bool,
                        dsn: str = RAG_DSN) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if not enabled:
            cur.execute("""
                SELECT a.username, bool_or(rp.permission_code='account.manage')
                FROM system_account a
                LEFT JOIN system_account_role ar ON ar.account_id=a.id
                LEFT JOIN system_role_permission rp ON rp.role_id=ar.role_id
                WHERE a.id=%s GROUP BY a.username
            """, (account_id,))
            row = cur.fetchone()
            username, is_admin = (row[0], row[1]) if row else (None, False)
            # 1) 内置管理员（admin 账户）不可停用
            if username == "admin":
                raise ValueError("系统内置管理员不可停用")
            # 2) 兜底：停用后必须仍存在启用的管理员（拥有 account.manage 权限）
            if is_admin:
                cur.execute("""
                    SELECT 1 FROM system_account a
                    JOIN system_account_role ar ON ar.account_id=a.id
                    JOIN system_role_permission rp ON rp.role_id=ar.role_id
                    WHERE a.enabled AND rp.permission_code='account.manage'
                      AND a.id<>%s LIMIT 1
                """, (account_id,))
                if not cur.fetchone():
                    raise ValueError("至少需保留一个启用的管理员账户，不可停用")
        cur.execute("UPDATE system_account SET enabled=%s WHERE id=%s",
                    (enabled, account_id))
        if not enabled:
            cur.execute("DELETE FROM sessions WHERE account_id=%s", (account_id,))
        conn.commit()


def reset_account_password(account_id: int, new_password: str | None = None,
                           dsn: str = RAG_DSN) -> str:
    """管理员重置密码：随机或指定；强制首登改密。"""
    from src.auth.auth import hash_password
    import secrets
    pw = new_password or secrets.token_urlsafe(12)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE system_account SET password_hash=%s, must_change_password=true
            WHERE id=%s
        """, (hash_password(pw), account_id))
        cur.execute("DELETE FROM sessions WHERE account_id=%s", (account_id,))
        conn.commit()
    return pw


# ================= 角色管理服务 =================

def list_roles(dsn: str = RAG_DSN) -> list[dict]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT r.id, r.code, r.name, r.description, r.data_scope, r.builtin,
                   coalesce(string_agg(rp.permission_code, ',' ORDER BY rp.permission_code), '')
            FROM system_role r
            LEFT JOIN system_role_permission rp ON rp.role_id = r.id
            GROUP BY r.id ORDER BY r.id
        """)
        return [{
            "id": row[0], "code": row[1], "name": row[2], "description": row[3],
            "data_scope": row[4], "builtin": row[5],
            "permissions": [p for p in row[6].split(",") if p],
        } for row in cur.fetchall()]


def create_role(code: str, name: str, description: str,
                data_scope: str, permission_codes: list[str],
                dsn: str = RAG_DSN) -> dict:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM system_role WHERE code=%s", (code,))
        if cur.fetchone():
            raise ValueError(f"角色已存在: {code}")
        cur.execute("""
            INSERT INTO system_role (code, name, description, data_scope)
            VALUES (%s,%s,%s,%s) RETURNING id
        """, (code, name, description, data_scope))
        rid = cur.fetchone()[0]
        for p in permission_codes:
            cur.execute("""
                INSERT INTO system_role_permission (role_id, permission_code)
                VALUES (%s,%s) ON CONFLICT DO NOTHING
            """, (rid, p))
        conn.commit()
    return next(r for r in list_roles(dsn) if r["id"] == rid)


def update_role_permissions(role_id: int, permission_codes: list[str],
                            dsn: str = RAG_DSN) -> None:
    """替换式重设角色权限。"""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM system_role_permission WHERE role_id=%s",
                    (role_id,))
        for p in permission_codes:
            cur.execute("""
                INSERT INTO system_role_permission (role_id, permission_code)
                VALUES (%s,%s) ON CONFLICT DO NOTHING
            """, (role_id, p))
        conn.commit()


def delete_role(role_id: int, dsn: str = RAG_DSN) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT builtin FROM system_role WHERE id=%s", (role_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("角色不存在")
        if row[0]:
            raise ValueError("内置角色不可删除")
        cur.execute("DELETE FROM system_role_permission WHERE role_id=%s",
                    (role_id,))
        cur.execute("DELETE FROM system_account_role WHERE role_id=%s",
                    (role_id,))
        cur.execute("DELETE FROM system_role WHERE id=%s", (role_id,))
        conn.commit()


def list_permissions(dsn: str = RAG_DSN) -> list[dict]:
    """权限点 → 模块/页面/功能树（前端勾选用）。"""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT code, name, module, page FROM system_permission ORDER BY sort, code")
        return [{"code": r[0], "name": r[1], "module": r[2], "page": r[3]}
                for r in cur.fetchall()]
