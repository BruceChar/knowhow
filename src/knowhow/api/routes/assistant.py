"""Sales workbench and interaction capture APIs."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from knowhow.api.deps import current_account
from knowhow.services import assistant

router = APIRouter(tags=["assistant"])


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=5000)
    channel: str = Field(default="web", max_length=50)


class InteractionUpdateBody(BaseModel):
    final_answer: str = Field(default="", max_length=20000)
    status: Literal["draft", "copied", "sent", "escalated"]


@router.post("/api/ask")
def ask(body: AskBody, user: dict = Depends(current_account)):
    return assistant.answer_question(
        user["organization_id"], user["id"],
        body.question.strip(), channel=body.channel,
    )


@router.put("/api/interactions/{interaction_id}")
def update_interaction(
    interaction_id: int,
    body: InteractionUpdateBody,
    user: dict = Depends(current_account),
):
    try:
        return assistant.update_interaction(
            user["organization_id"], user["id"], interaction_id,
            final_answer=body.final_answer.strip(), status=body.status,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

