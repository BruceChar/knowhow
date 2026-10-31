"""Knowledge and gap APIs."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from knowhow.api.deps import current_account, require_permission
from knowhow.services import knowledge

router = APIRouter(tags=["knowledge"])


class KnowledgeBody(BaseModel):
    kind: Literal["faq", "product", "policy", "playbook", "case", "sop"] = "faq"
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(default="", max_length=5000)
    answer: str = Field(min_length=1, max_length=20000)
    risk_level: Literal["low", "medium", "high"] = "low"
    source_ref: str = Field(default="", max_length=1000)
    valid_until: str | None = None
    scope: Literal["organization", "team", "private"] = "organization"
    team_id: int | None = None
    owner_id: int | None = None
    metadata: dict = Field(default_factory=dict)


class ImportBody(BaseModel):
    content: str = Field(min_length=1, max_length=500000)


class GapAnswerBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=20000)
    source_ref: str = Field(default="", max_length=1000)


class GapIgnoreBody(BaseModel):
    note: str = Field(default="不纳入知识库", max_length=2000)


@router.get("/api/knowledge")
def list_knowledge(
    status: str = "",
    kind: str = "",
    q: str = "",
    user: dict = Depends(require_permission("knowledge.view")),
):
    return {"items": knowledge.list_knowledge(
        user["organization_id"], user["id"],
        status=status or None,
        kind=kind or None,
        query=q,
    )}


@router.post("/api/knowledge")
def create_knowledge(
    body: KnowledgeBody,
    user: dict = Depends(require_permission("knowledge.suggest")),
):
    try:
        return knowledge.create_knowledge(
            user["organization_id"], user["id"], **body.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/knowledge/owners")
def list_owners(
    user: dict = Depends(require_permission("knowledge.edit")),
):
    return {"items": knowledge.list_owners(user["organization_id"])}


@router.put("/api/knowledge/{knowledge_id}")
def update_knowledge(
    knowledge_id: int,
    body: KnowledgeBody,
    user: dict = Depends(require_permission("knowledge.edit")),
):
    try:
        return knowledge.update_knowledge(
            user["organization_id"], knowledge_id, account_id=user["id"],
            **body.model_dump(),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/knowledge/{knowledge_id}/publish")
def publish_knowledge(
    knowledge_id: int,
    user: dict = Depends(require_permission("knowledge.publish")),
):
    try:
        return knowledge.publish_knowledge(
            user["organization_id"], user["id"], knowledge_id,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/knowledge/{knowledge_id}/archive")
def archive_knowledge(
    knowledge_id: int,
    user: dict = Depends(require_permission("knowledge.publish")),
):
    try:
        knowledge.archive_knowledge(
            user["organization_id"], user["id"], knowledge_id,
        )
        return {"ok": True}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/knowledge/{knowledge_id}/embedding")
def rebuild_embedding(
    knowledge_id: int,
    user: dict = Depends(require_permission("knowledge.edit")),
):
    try:
        return knowledge.rebuild_embedding(
            user["organization_id"], knowledge_id,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/knowledge/import-text")
def import_text(
    body: ImportBody,
    user: dict = Depends(require_permission("knowledge.publish")),
):
    return knowledge.import_faq_text(
        user["organization_id"], user["id"], body.content,
    )


@router.get("/api/gaps")
def list_gaps(status: str = "open", user: dict = Depends(current_account)):
    return {"items": knowledge.list_gaps(user["organization_id"], status)}


@router.post("/api/gaps/{gap_id}/answer")
def answer_gap(
    gap_id: int,
    body: GapAnswerBody,
    user: dict = Depends(require_permission("gap.manage")),
):
    return knowledge.resolve_gap_with_knowledge(
        user["organization_id"], user["id"], gap_id, **body.model_dump(),
    )


@router.post("/api/gaps/{gap_id}/ignore")
def ignore_gap(
    gap_id: int,
    body: GapIgnoreBody,
    user: dict = Depends(require_permission("gap.manage")),
):
    try:
        knowledge.ignore_gap(user["organization_id"], gap_id, body.note)
        return {"ok": True}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

