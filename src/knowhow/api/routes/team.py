"""Team account API."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from knowhow.api.deps import current_account, require_permission
from knowhow.services import team

router = APIRouter(tags=["team"])


class AccountBody(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)
    role_id: int
    team_ids: list[int] = Field(default_factory=list)


class EnabledBody(BaseModel):
    enabled: bool


class RoleBody(BaseModel):
    code: str = Field(min_length=2, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=80)
    permissions: list[str] = Field(default_factory=list)


class RolePermissionsBody(BaseModel):
    permissions: list[str]


class TeamBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class AccountTeamsBody(BaseModel):
    team_ids: list[int]


@router.get("/api/team")
def list_team(user: dict = Depends(require_permission("team.manage"))):
    return {"items": team.list_accounts(user["organization_id"])}


@router.post("/api/team")
def create_account(
    body: AccountBody,
    user: dict = Depends(require_permission("team.manage")),
):
    try:
        return team.create_account(
            user["organization_id"], **body.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            raise HTTPException(409, "登录账号已存在") from exc
        raise


@router.put("/api/team/{account_id}/enabled")
def set_enabled(
    account_id: int,
    body: EnabledBody,
    user: dict = Depends(require_permission("team.manage")),
):
    try:
        team.set_account_enabled(
            user["organization_id"], account_id, body.enabled,
        )
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/roles")
def list_roles(user: dict = Depends(require_permission("role.manage"))):
    return {"items": team.list_roles(user["organization_id"])}


@router.post("/api/roles")
def create_role(
    body: RoleBody,
    user: dict = Depends(require_permission("role.manage")),
):
    try:
        return team.create_role(user["organization_id"], **body.model_dump())
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            raise HTTPException(409, "角色编码已存在") from exc
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/roles/{role_id}/permissions")
def update_role_permissions(
    role_id: int,
    body: RolePermissionsBody,
    user: dict = Depends(require_permission("role.manage")),
):
    try:
        return team.update_role_permissions(
            user["organization_id"], role_id, body.permissions,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/teams")
def list_my_teams(user: dict = Depends(current_account)):
    return {
        "items": team.list_teams_for_account(
            user["organization_id"], user["id"],
        )
    }


@router.post("/api/teams")
def create_team(
    body: TeamBody,
    user: dict = Depends(require_permission("team.manage")),
):
    try:
        return team.create_team(user["organization_id"], body.name)
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            raise HTTPException(409, "团队名称已存在") from exc
        raise


@router.put("/api/team/{account_id}/teams")
def set_account_teams(
    account_id: int,
    body: AccountTeamsBody,
    user: dict = Depends(require_permission("team.manage")),
):
    try:
        team.set_account_teams(
            user["organization_id"], account_id, body.team_ids,
        )
        return {"ok": True}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

