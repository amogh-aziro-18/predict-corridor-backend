"""Telemetry node — collects live operational status via existing services."""

from __future__ import annotations

from typing import Any, Callable

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.agents.state import AgentState
from app.observability.langfuse import node_span
from app.services.dashboard_service import DashboardService
from app.services.telemetry_service import TelemetryService


def build_telemetry_node(
    db: Session,
    trace: Any | None,
) -> Callable[[AgentState], dict[str, Any]]:
    def telemetry_node(state: AgentState) -> dict[str, Any]:
        with node_span(trace, "telemetry_node") as span:
            dashboard = DashboardService(db).water_dashboard()
            live = TelemetryService(db).latest_grouped_by_asset(utility="water")

            telemetry = {
                "utility": dashboard.utility,
                "overall_health": dashboard.overall_health,
                "critical_assets": dashboard.critical_assets,
                "active_alerts": dashboard.active_alerts,
                "average_pressure": dashboard.average_pressure,
                "average_flow": dashboard.average_flow,
                "asset_count": dashboard.asset_count,
                "simulator_status": dashboard.simulator_status,
                "last_telemetry_at": jsonable_encoder(dashboard.last_telemetry_at),
                "live_telemetry_points": dashboard.live_telemetry_points,
                "data_freshness_seconds": dashboard.data_freshness_seconds,
                "zone_summary": jsonable_encoder(dashboard.zone_summary),
                "assets": [
                    {
                        "asset_id": item.asset_id,
                        "asset_name": item.asset_name,
                        "zone": item.zone,
                        "status": item.status,
                        "health_score": item.health_score,
                        "metric_count": len(item.metrics),
                    }
                    for item in live
                ],
            }
            span["output"] = {
                "overall_health": telemetry["overall_health"],
                "critical_assets": telemetry["critical_assets"],
                "active_alerts": telemetry["active_alerts"],
                "live_assets": len(telemetry["assets"]),
            }
            return {
                "telemetry": telemetry,
                "execution_status": {"telemetry_node": "completed"},
            }

    return telemetry_node
