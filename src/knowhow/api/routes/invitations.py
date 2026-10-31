"""Invitation and LAN runtime endpoints."""
from __future__ import annotations

import segno
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from knowhow.api.deps import SESSION_COOKIE, require_permission
from knowhow.security import create_session, session_account
from knowhow.services import invitations
from knowhow.settings import settings

router = APIRouter(tags=["invitations"])


class InviteBody(BaseModel):
    role_id: int
    team_id: int
    expires_hours: int = Field(default=72, ge=1, le=720)


class JoinBody(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)


def _set_session(response: Response, account_id: int) -> str:
    token = create_session(account_id)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=settings.cookie_secure, max_age=settings.session_hours * 3600,
    )
    return token


@router.get("/api/runtime/info")
def runtime_info(
    request: Request,
    user: dict = Depends(require_permission("team.manage")),
):
    return invitations.runtime_info(request.url.port or 8000)


@router.post("/api/invites")
def create_invite(
    body: InviteBody,
    request: Request,
    user: dict = Depends(require_permission("team.manage")),
):
    base_url = f"http://{invitations.lan_ip()}:{request.url.port or 8000}"
    try:
        invite = invitations.create_invite(
            user["organization_id"], user["id"],
            role_id=body.role_id, team_id=body.team_id,
            base_url=base_url, expires_hours=body.expires_hours,
        )
        qr = segno.make(invite["url"], error="m")
        invite["qr_svg"] = qr.svg_inline(scale=4, border=2)
        return invite
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/invites/{token}")
def inspect_invite(token: str):
    try:
        return invitations.inspect_invite(token)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(410, str(exc)) from exc


@router.post("/api/invites/{token}/accept")
def accept_invite(token: str, body: JoinBody, response: Response):
    try:
        account = invitations.accept_invite(
            token, username=body.username,
            display_name=body.display_name, password=body.password,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    token_value = _set_session(response, account["id"])
    user = session_account(token_value)
    return {
        "ok": True,
        "user": user,
    }

