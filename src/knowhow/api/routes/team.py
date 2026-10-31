"""Team account API."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from knowhow.api.deps import require_roles
from knowhow.services import team

router = APIRouter(tags=["team"])


class AccountBody(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)
    role: Literal["sales", "steward", "admin"] = "sales"


class EnabledBody(BaseModel):
    enabled: bool


@router.get("/api/team")
def list_team(user: dict = Depends(require_roles("owner", "admin"))):
    return {"items": team.list_accounts(user["organization_id"])}


@router.post("/api/team")
def create_account(
    body: AccountBody,
    user: dict = Depends(require_roles("owner", "admin")),
):
    try:
        return team.create_account(
            user["organization_id"], **body.model_dump(),
        )
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            raise HTTPException(409, "登录账号已存在") from exc
        raise


@router.put("/api/team/{account_id}/enabled")
def set_enabled(
    account_id: int,
    body: EnabledBody,
    user: dict = Depends(require_roles("owner", "admin")),
):
    try:
        team.set_account_enabled(
            user["organization_id"], account_id, body.enabled,
        )
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

