"""GET /dashboard/water|power|gas|communications|corridor — live operations dashboards."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.dashboard import (
    CommunicationsDashboardOut,
    CorridorDashboardOut,
    GasDashboardOut,
    PowerDashboardOut,
    WaterDashboardOut,
)
from app.services.dashboard_service import DashboardService

router = APIRouter()


@router.get("/dashboard/corridor", response_model=CorridorDashboardOut)
def corridor_dashboard(db: Session = Depends(db_dep)) -> CorridorDashboardOut:
    """
    Overall corridor operational posture composed from live Water + Power + Gas
    + Communications dashboards.

    Categorical posture only — not a fabricated 0–100 corridor health score.
    """
    return DashboardService(db).corridor_dashboard()


@router.get("/dashboard/water", response_model=WaterDashboardOut)
def water_dashboard(db: Session = Depends(db_dep)) -> WaterDashboardOut:
    return DashboardService(db).water_dashboard()


@router.get("/dashboard/power", response_model=PowerDashboardOut)
def power_dashboard(db: Session = Depends(db_dep)) -> PowerDashboardOut:
    """Rule-based Power condition assessment from live telemetry (not ML)."""
    return DashboardService(db).power_dashboard()


@router.get("/dashboard/gas", response_model=GasDashboardOut)
def gas_dashboard(db: Session = Depends(db_dep)) -> GasDashboardOut:
    """Rule-based Gas condition assessment from live telemetry (not ML)."""
    return DashboardService(db).gas_dashboard()


@router.get("/dashboard/communications", response_model=CommunicationsDashboardOut)
def communications_dashboard(db: Session = Depends(db_dep)) -> CommunicationsDashboardOut:
    """Rule-based Communications condition assessment from live telemetry (not ML)."""
    return DashboardService(db).communications_dashboard()
