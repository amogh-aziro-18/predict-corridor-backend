"""GET /dashboard/water — live water operations dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.dashboard import WaterDashboardOut
from app.services.dashboard_service import DashboardService

router = APIRouter()


@router.get("/dashboard/water", response_model=WaterDashboardOut)
def water_dashboard(db: Session = Depends(db_dep)) -> WaterDashboardOut:
    return DashboardService(db).water_dashboard()
