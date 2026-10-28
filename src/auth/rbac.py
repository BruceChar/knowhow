"""RBAC 权限模型：四角色 + 数据级权限过滤。

角色（role）：
  - admin        管理员：全部权限 + 角色管理
  - reviewer     知识审核员：审核流（approve/reject/deprecate）+ 打标
  - editor       业务员/知识录入：上传/编辑/打标（不可审核）
  - agent        客服：只读检索问答

数据级权限（permission 列）：public / internal / confidential
  - 客服（agent）可见 public + internal
  - 业务员/审核员/管理员可见全部（internal + confidential）

权限用装饰器挂在路由上，数据级用 SQL 过滤（在查询层注入）。
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Callable

import psycopg
from fastapi import Depends, HTTPException, Request

from src.config import RAG_DSN

ROLE_ORDER = {"agent": 0, "editor": 1, "reviewer": 2, "admin": 3}

# 数据级可见性（最小权限）
DATA_PERMISSION_VISIBLE = {
    "agent": ["public", "internal"],
    "editor": ["public", "internal", "confidential"],
    "reviewer": ["public", "internal", "confidential"],
    "admin": ["public", "internal", "confidential"],
}


@dataclass
class User:
    """简化用户模型：真实系统接企业账号体系（OAuth/LDAP）时替换。"""
    id: str
    name: str
    role: str

    @property
    def level(self) -> int:
        return ROLE_ORDER.get(self.role, 0)

    def can(self, min_role: str) -> bool:
        return self.level >= ROLE_ORDER.get(min_role, 0)


VALID_ROLES = set(ROLE_ORDER.keys())


def list_users(dsn: str = RAG_DSN) -> list[dict]:
    """Return users from the authoritative database, never process memory."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, role FROM users ORDER BY id")
        return [{"id": row[0], "name": row[1], "role": row[2]}
                for row in cur.fetchall()]


def create_user(uid: str, name: str, role: str,
                initial_password: str | None = None,
                dsn: str = RAG_DSN) -> tuple[User, str | None]:
    """Create a persistent user and return an optional one-time password."""
    if role not in VALID_ROLES:
        raise ValueError(f"非法角色 {role!r}，可选 {sorted(VALID_ROLES)}")
    from src.auth.auth import hash_password

    temp_password = initial_password
    if not temp_password:
        temp_password = secrets.token_urlsafe(12)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE id=%s", (uid,))
        if cur.fetchone():
            raise ValueError(f"用户已存在: {uid}")
        cur.execute("""
            INSERT INTO users (id, name, role, password_hash, must_change_password)
            VALUES (%s,%s,%s,%s,true)
        """, (uid, name, role, hash_password(temp_password)))
        conn.commit()
    return User(uid, name, role), (None if initial_password else temp_password)


def update_user_role(uid: str, role: str, dsn: str = RAG_DSN) -> User:
    """Persist role changes so the next request sees the new authorization."""
    if role not in VALID_ROLES:
        raise ValueError(f"非法角色 {role!r}，可选 {sorted(VALID_ROLES)}")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET role=%s WHERE id=%s RETURNING id,name,role",
            (role, uid),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise ValueError(f"用户不存在: {uid}")
    return User(row[0], row[1], row[2])


def get_current_user(request: Request) -> User:
    """解析当前用户，优先级：
    1) Cookie kh_session —— 生产会话（httpOnly，防 XSS 窃取）
    2) Authorization: Bearer <token> —— 兼容 API/脚本调用
    3) X-User-Id —— 仅在显式设置 KNOWHOW_DEV_AUTH=1 时允许
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
        return User(info["id"], info["name"], info["role"])
    if os.environ.get("KNOWHOW_DEV_AUTH", "0") == "1":
        uid = request.headers.get("X-User-Id", "u_agent")
        with psycopg.connect(RAG_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT id,name,role FROM users WHERE id=%s", (uid,))
            row = cur.fetchone()
        if row:
            return User(row[0], row[1], row[2])
    raise HTTPException(status_code=401, detail="请先登录")


def require_role(min_role: str) -> Callable:
    """路由级 RBAC：低于指定角色直接 403。"""
    def dep(user: User = Depends(get_current_user)) -> User:
        if not user.can(min_role):
            raise HTTPException(status_code=403,
                                detail=f"需要 {min_role} 及以上权限")
        return user
    return dep


def data_permission_clause(user: User, alias: str = "") -> str:
    """数据级 SQL 过滤：返回 permission IN (...) 子句（防越权读保密数据）。"""
    visible = DATA_PERMISSION_VISIBLE.get(user.role, ["public"])
    quoted = ", ".join(f"'{p}'" for p in visible)
    prefix = f"{alias}." if alias else ""
    return f"{prefix}permission IN ({quoted})"
