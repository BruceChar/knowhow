"""New-hire practice endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from knowhow.api.deps import require_permission
from knowhow.services import training

router = APIRouter(tags=["training"])


class AttemptBody(BaseModel):
    knowledge_id: int
    response: str = Field(min_length=1, max_length=10000)


@router.get("/api/training/quiz")
def get_quiz(user: dict = Depends(require_permission("training.use"))):
    return training.quiz(user["organization_id"], user["id"])


@router.get("/api/training/readiness")
def get_readiness(user: dict = Depends(require_permission("training.use"))):
    return training.readiness(user["organization_id"], user["id"])


@router.post("/api/training/attempts")
def submit_attempt(
    body: AttemptBody,
    user: dict = Depends(require_permission("training.use")),
):
    try:
        return training.submit_attempt(
            user["organization_id"], user["id"],
            body.knowledge_id, body.response.strip(),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

