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

from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request

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


# ---------------- 用户解析（测试用静态表，生产换 SSO） ----------------
# 注：真实部署时从会话/JWT 解析，这里用 X-User-Role 头模拟（演示与联调用）
STATIC_USERS = {
    "u_admin": User("u_admin", "管理员", "admin"),
    "u_reviewer": User("u_reviewer", "审核员", "reviewer"),
    "u_editor": User("u_editor", "业务员", "editor"),
    "u_agent": User("u_agent", "客服", "agent"),
}


def get_current_user(request: Request) -> User:
    """从请求解析当前用户。开发环境用 X-User-Id 头；生产接 JWT。"""
    uid = request.headers.get("X-User-Id", "u_agent")
    user = STATIC_USERS.get(uid)
    if user is None:
        raise HTTPException(status_code=401, detail="未知用户")
    return user


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
