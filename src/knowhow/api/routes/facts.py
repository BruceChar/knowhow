"""Product and price-fact endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from knowhow.api.deps import require_permission
from knowhow.services import facts

router = APIRouter(tags=["facts"])


@router.get("/api/facts")
def list_facts(user: dict = Depends(require_permission("facts.view"))):
    return {"items": facts.list_facts(user["organization_id"])}


@router.post("/api/facts/import")
async def import_facts(
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("facts.manage")),
):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "产品报价当前支持 CSV")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "产品报价文件不能超过 10MB")
    return facts.import_facts_csv(
        user["organization_id"], user["id"], content,
    )

