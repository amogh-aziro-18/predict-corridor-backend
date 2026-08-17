"""Water dashboard aggregation service — telemetry-driven health & alerts."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.api.schemas.dashboard import WaterDashboardOut, ZoneWaterSummaryOut
from app.domain.repositories import AssetRepository, TelemetryRepository, ZoneRepository
from app.utilities.water import health as water_health


class DashboardService:
    def __init__(self, db: Session) -> None:
        self._assets = AssetRepository(db)
        self._zones = ZoneRepository(db)
        self._telemetry = TelemetryRepository(db)

    def water_dashboard(self) -> WaterDashboardOut:
        assets = self._assets.list(utility_kind="water")
        zones = {z.id: z for z in self._zones.list()}
        latest = self._telemetry.latest_rows(utility_kind="water")

        metrics_by_asset: dict[str, dict[str, float]] = defaultdict(dict)
        latest_at: datetime | None = None
        for asset_id, _name, tel in latest:
            metrics_by_asset[asset_id][tel.metric_key] = float(tel.value)
            if latest_at is None or tel.recorded_at > latest_at:
                latest_at = tel.recorded_at

        pressure_values: list[float] = []
        flow_values: list[float] = []
        pressure_by_zone: dict[str, list[float]] = defaultdict(list)
        flow_by_zone: dict[str, list[float]] = defaultdict(list)
        level_by_zone: dict[str, list[float]] = defaultdict(list)
        health_by_zone: dict[str, list[float]] = defaultdict(list)

        asset_by_id = {a.id: a for a in assets}
        corridor_flows = [
            m["flow_rate"] for m in metrics_by_asset.values() if "flow_rate" in m
        ]
        flow_reference = (
            sum(corridor_flows) / len(corridor_flows) if corridor_flows else None
        )

        asset_health: dict[str, float] = {}
        asset_alerts: dict[str, list[str]] = {}
        critical_count = 0
        alert_count = 0
        degraded_by_zone: dict[str, int] = defaultdict(int)

        for asset in assets:
            metrics = metrics_by_asset.get(asset.id, {})
            # Prefer live level keys; fall back to profile mirror.
            level = (
                metrics.get("tank_level")
                or metrics.get("reservoir_level")
                or (
                    float(asset.profile["current_level"])
                    if isinstance(asset.profile, dict)
                    and isinstance(asset.profile.get("current_level"), (int, float))
                    else None
                )
            )
            pressure = metrics.get("pressure")
            flow = metrics.get("flow_rate")
            if pressure is not None:
                pressure_values.append(pressure)
                pressure_by_zone[asset.zone_id].append(pressure)
            if flow is not None:
                flow_values.append(flow)
                flow_by_zone[asset.zone_id].append(flow)
            if level is not None:
                level_by_zone[asset.zone_id].append(level)

            computed = water_health.weighted_health(
                pressure=pressure,
                level=level,
                flow=flow,
                flow_reference=flow_reference,
                pump_running=metrics.get("pump_running"),
                pump_speed=metrics.get("pump_speed"),
            )
            # Blend with stored health for gradual movement.
            blended = water_health.blend_health(float(asset.health), computed)
            asset_health[asset.id] = blended
            health_by_zone[asset.zone_id].append(blended)

            alerts = water_health.evaluate_alerts(metrics)
            asset_alerts[asset.id] = alerts
            if alerts:
                alert_count += len(alerts)
            status = water_health.status_from_alerts(alerts, current=asset.status)
            if status in {"degraded", "maintenance", "offline"}:
                degraded_by_zone[asset.zone_id] += 1
            if water_health.is_critical_asset(
                criticality=asset.criticality,
                alerts=alerts,
                health=blended,
            ):
                critical_count += 1

        def avg(values: list[float]) -> float | None:
            return round(sum(values) / len(values), 2) if values else None

        overall_health = (
            round(sum(asset_health.values()) / len(asset_health), 1)
            if asset_health
            else 90.0
        )

        zone_summary: list[ZoneWaterSummaryOut] = []
        assets_by_zone: dict[str, list] = defaultdict(list)
        for a in assets:
            assets_by_zone[a.zone_id].append(a)

        for zone_id, zone_assets in sorted(assets_by_zone.items()):
            zone = zones.get(zone_id)
            zone_health_vals = health_by_zone.get(zone_id) or [
                float(a.health) for a in zone_assets
            ]
            zone_summary.append(
                ZoneWaterSummaryOut(
                    zone_id=zone_id,
                    zone_name=zone.name if zone else zone_id,
                    asset_count=len(zone_assets),
                    average_health=round(
                        sum(zone_health_vals) / len(zone_health_vals), 1
                    ),
                    average_pressure=avg(pressure_by_zone.get(zone_id, [])),
                    average_flow=avg(flow_by_zone.get(zone_id, [])),
                    degraded_assets=degraded_by_zone.get(zone_id, 0),
                )
            )

        freshness = water_health.freshness_seconds(latest_at)
        if freshness is None:
            sim_status = "idle"
        elif freshness <= 12:
            sim_status = "running"
        elif freshness <= 60:
            sim_status = "slow"
        else:
            sim_status = "stale"

        return WaterDashboardOut(
            overall_health=overall_health,
            average_pressure=avg(pressure_values),
            average_flow=avg(flow_values),
            critical_assets=critical_count,
            active_alerts=alert_count,
            zone_summary=zone_summary,
            asset_count=len(assets),
            utility="water",
            last_telemetry_at=latest_at,
            live_telemetry_points=len(latest),
            data_freshness_seconds=round(freshness, 1) if freshness is not None else None,
            simulator_status=sim_status,
            backend_status="online",
            api_status="healthy",
        )
