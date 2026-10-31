"""Knowledge import preview and execution endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from knowhow.api.deps import require_permission
from knowhow.services import imports

router = APIRouter(tags=["imports"])


@router.post("/api/imports/preview")
async def preview(
    file: UploadFile = File(...),
    source_type: str = Form("auto"),
    user: dict = Depends(require_permission("knowledge.edit")),
):
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "导入文件不能超过 20MB")
    suffix = (file.filename or "").lower().rsplit(".", 1)[-1]
    if suffix not in {"csv", "txt", "md", "markdown"}:
        raise HTTPException(400, "当前支持 CSV、TXT、Markdown 文件")
    try:
        return imports.preview_import(
            user["organization_id"], user["id"],
            filename=file.filename or f"upload.{suffix}",
            content=content, source_type=source_type,
        )
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "无法识别文件编码，请使用 UTF-8") from exc


@router.post("/api/imports/{batch_id}/execute")
def execute(
    batch_id: int,
    user: dict = Depends(require_permission("knowledge.edit")),
):
    try:
        return imports.execute_import(
            user["organization_id"], user["id"], batch_id,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/imports")
def list_batches(user: dict = Depends(require_permission("knowledge.view"))):
    return {"items": imports.list_imports(user["organization_id"])}

