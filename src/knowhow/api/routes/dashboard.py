"""Owner dashboard API."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from knowhow.api.deps import require_permission
from knowhow.services.dashboard import dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard")
def owner_dashboard(
    user: dict = Depends(require_permission("dashboard.view")),
):
    return dashboard(user["organization_id"])

