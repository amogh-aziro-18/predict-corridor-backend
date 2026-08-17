"""Water dashboard response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ZoneWaterSummaryOut(BaseModel):
    zone_id: str
    zone_name: str
    asset_count: int
    average_health: float
    average_pressure: float | None = None
    average_flow: float | None = None
    degraded_assets: int = 0


class WaterDashboardOut(BaseModel):
    overall_health: float
    average_pressure: float | None = None
    average_flow: float | None = None
    critical_assets: int
    active_alerts: int
    zone_summary: list[ZoneWaterSummaryOut] = Field(default_factory=list)
    asset_count: int = 0
    utility: str = "water"
    # Status-strip fields (reuse existing frontend StatusCards)
    last_telemetry_at: datetime | None = None
    live_telemetry_points: int = 0
    data_freshness_seconds: float | None = None
    simulator_status: str = "unknown"
    backend_status: str = "online"
    api_status: str = "healthy"
