"""Owner dashboard API."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from knowhow.api.deps import require_roles
from knowhow.services.dashboard import dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard")
def owner_dashboard(
    user: dict = Depends(require_roles("owner", "steward", "admin")),
):
    return dashboard(user["organization_id"])

