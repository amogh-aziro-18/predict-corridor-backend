"""Water / Power dashboard aggregation — telemetry-driven health & alerts."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from typing import Any

from app.api.schemas.dashboard import (
    CommunicationsDashboardOut,
    CorridorDashboardOut,
    CorridorDependencyAffectedAssetOut,
    CorridorDependencyExposureOut,
    CorridorDependencyPathOut,
    CorridorDependencySourceOut,
    CorridorUtilityPostureOut,
    GasDashboardOut,
    PowerDashboardOut,
    WaterDashboardOut,
    ZoneCommunicationsSummaryOut,
    ZoneGasSummaryOut,
    ZonePowerSummaryOut,
    ZoneWaterSummaryOut,
)
from app.domain.repositories import AssetRepository, TelemetryRepository, ZoneRepository
from app.utilities.communications import health as communications_health
from app.utilities.gas import health as gas_health
from app.utilities.power import health as power_health
from app.utilities.water import health as water_health

_CONDITION_RANK = {
    "unavailable": -1,
    "healthy": 0,
    "warning": 1,
    "high_risk": 2,
    "critical": 3,
}

# Corridor operational posture (categorical — not a 0–100 scientific index).
_POSTURE_RANK = {
    "UNAVAILABLE": -1,
    "HEALTHY": 0,
    "STABLE": 1,
    "DEGRADED": 2,
    "CRITICAL": 3,
}


def _aggregate_condition(conditions: list[str]) -> str:
    """
    Zone/corridor condition = worst non-unavailable asset condition.

    If every asset is unavailable (no telemetry), return unavailable.
    """
    usable = [c for c in conditions if c != "unavailable"]
    if not usable:
        return "unavailable" if conditions else "unavailable"
    return max(usable, key=lambda c: _CONDITION_RANK.get(c, 0))


def _gas_is_critical_asset(
    *,
    criticality: str,
    condition: str,
    alerts: list[str],
    health: float,
) -> bool:
    """High-criticality Gas assets under severe rule-based stress (not ML)."""
    if criticality not in {"very-high", "high"}:
        return False
    if condition == "critical":
        return True
    if any("critical" in str(code).lower() for code in alerts):
        return True
    return health < 70.0


def water_signals_to_posture(
    *,
    overall_health: float,
    critical_assets: int,
    active_alerts: int,
    api_status: str,
) -> str:
    """Map live Water dashboard signals → categorical posture."""
    if api_status in {"unreachable", "error"}:
        return "UNAVAILABLE"
    if overall_health < 55.0 or (critical_assets >= 2 and active_alerts >= 2):
        return "CRITICAL"
    if overall_health < 75.0 or critical_assets >= 1 or active_alerts >= 3:
        return "DEGRADED"
    if overall_health < 90.0 or active_alerts > 0:
        return "STABLE"
    return "HEALTHY"


def power_condition_to_posture(overall_condition: str, *, api_status: str) -> str:
    """Map live Power overall_condition → categorical posture."""
    if api_status in {"unreachable", "error"}:
        return "UNAVAILABLE"
    cond = (overall_condition or "").strip().lower()
    if cond == "critical":
        return "CRITICAL"
    if cond in {"high_risk", "warning"}:
        return "DEGRADED" if cond == "high_risk" else "STABLE"
    if cond == "unavailable":
        return "UNAVAILABLE"
    if cond == "healthy":
        return "HEALTHY"
    return "STABLE"


def gas_condition_to_posture(overall_condition: str, *, api_status: str) -> str:
    """
    Map live Gas overall_condition → categorical posture.

    Uses the same condition vocabulary as Power (rule-based Gas dashboard).
    """
    return power_condition_to_posture(overall_condition, api_status=api_status)


def communications_condition_to_posture(overall_condition: str, *, api_status: str) -> str:
    """
    Map live Communications overall_condition → categorical posture.

    Same condition vocabulary as Power/Gas (including unavailable for link_down).
    """
    return power_condition_to_posture(overall_condition, api_status=api_status)


def _communications_is_critical_asset(
    *,
    criticality: str,
    condition: str,
    alerts: list[str],
    health: float,
) -> bool:
    """High-criticality Communications assets under severe rule-based stress."""
    if criticality not in {"very-high", "high"}:
        return False
    if condition in {"critical", "unavailable"}:
        return True
    if any("critical" in str(code).lower() or code == "link_down" for code in alerts):
        return True
    return health < 70.0


def compose_corridor_posture(utility_postures: list[str]) -> str:
    """Worst-of live utilities (ignore UNAVAILABLE when at least one live utility exists)."""
    usable = [p for p in utility_postures if p != "UNAVAILABLE"]
    if not usable:
        return "UNAVAILABLE"
    return max(usable, key=lambda p: _POSTURE_RANK.get(p, 0))


class DashboardService:
    # Phase 16.2 / 16.5 live dependency triggers (not static criticality).
    _DEPENDENCY_TRIGGERS = frozenset({"critical", "unavailable"})

    def __init__(self, db: Session) -> None:
        self._db = db
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
                    average_level=avg(level_by_zone.get(zone_id, [])),
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

    def power_dashboard(self) -> PowerDashboardOut:
        """
        Power dashboard from live telemetry + rule-based condition assessment.

        Does not invent KPIs when telemetry is missing — assets without readings
        contribute condition=unavailable.
        """
        assets = self._assets.list(utility_kind="power")
        zones = {z.id: z for z in self._zones.list()}
        latest = self._telemetry.latest_rows(utility_kind="power")

        metrics_by_asset: dict[str, dict[str, float]] = defaultdict(dict)
        latest_at: datetime | None = None
        for asset_id, _name, tel in latest:
            metrics_by_asset[asset_id][tel.metric_key] = float(tel.value)
            if latest_at is None or tel.recorded_at > latest_at:
                latest_at = tel.recorded_at

        asset_health: dict[str, float] = {}
        asset_condition: dict[str, str] = {}
        asset_drivers: dict[str, list[str]] = {}
        asset_alert_counts: dict[str, int] = {}
        critical_count = 0
        alert_count = 0
        evaluated_at_by_zone: dict[str, datetime] = {}

        for asset in assets:
            metrics = metrics_by_asset.get(asset.id, {})
            profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
            nominal = profile.get("nominal_voltage_kv")
            nominal_f = float(nominal) if isinstance(nominal, (int, float)) else None

            # Prefer profile assessment from PowerService sync when present and
            # telemetry exists; otherwise evaluate from latest metrics only.
            stored_condition = profile.get("condition")
            stored_codes = profile.get("condition_alert_codes")
            stored_drivers = profile.get("condition_drivers")
            if metrics and isinstance(stored_condition, str) and stored_condition:
                condition = stored_condition
                alerts = (
                    [str(c) for c in stored_codes]
                    if isinstance(stored_codes, list)
                    else power_health.evaluate_alerts(
                        metrics,
                        asset_type=asset.type,
                        nominal_voltage_kv=nominal_f,
                    )
                )
                drivers = (
                    [str(d) for d in stored_drivers]
                    if isinstance(stored_drivers, list)
                    else power_health.drivers_from_alerts(alerts)
                )
                blended = float(asset.health)
            elif metrics:
                result = power_health.evaluate_asset_condition(
                    asset_id=asset.id,
                    zone_id=asset.zone_id,
                    asset_type=asset.type,
                    metrics=metrics,
                    nominal_voltage_kv=nominal_f,
                    previous_health=float(asset.health),
                )
                condition = result.condition
                alerts = result.alert_codes
                drivers = result.drivers
                blended = result.health_score
            else:
                condition = "unavailable"
                alerts = []
                drivers = []
                blended = float(asset.health)

            asset_health[asset.id] = blended
            asset_condition[asset.id] = condition
            asset_drivers[asset.id] = drivers
            asset_alert_counts[asset.id] = len(alerts)
            alert_count += len(alerts)
            if power_health.is_critical_asset(
                criticality=asset.criticality,
                alerts=alerts,
                health=blended,
            ):
                critical_count += 1
            if condition != "unavailable":
                evaluated_at_by_zone[asset.zone_id] = datetime.now(timezone.utc)

        scored = [h for aid, h in asset_health.items() if asset_condition.get(aid) != "unavailable"]
        overall_health = round(sum(scored) / len(scored), 1) if scored else 0.0
        overall_condition = _aggregate_condition(list(asset_condition.values()))

        assets_by_zone: dict[str, list] = defaultdict(list)
        for a in assets:
            assets_by_zone[a.zone_id].append(a)

        zone_summary: list[ZonePowerSummaryOut] = []
        for zone_id, zone_assets in sorted(assets_by_zone.items()):
            zone = zones.get(zone_id)
            conditions = [asset_condition[a.id] for a in zone_assets]
            health_vals = [asset_health[a.id] for a in zone_assets]
            affected = [
                a.id
                for a in zone_assets
                if asset_condition[a.id] in {"warning", "high_risk", "critical"}
            ]
            driver_counts: dict[str, int] = defaultdict(int)
            for a in zone_assets:
                for d in asset_drivers.get(a.id, []):
                    driver_counts[d] += 1
            major_drivers = [
                d
                for d, _ in sorted(driver_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            ]
            zone_summary.append(
                ZonePowerSummaryOut(
                    zone_id=zone_id,
                    zone_name=zone.name if zone else zone_id,
                    asset_count=len(zone_assets),
                    overall_condition=_aggregate_condition(conditions),
                    average_health=round(sum(health_vals) / len(health_vals), 1)
                    if health_vals
                    else 0.0,
                    affected_assets=affected,
                    major_drivers=major_drivers,
                    active_alert_count=sum(asset_alert_counts.get(a.id, 0) for a in zone_assets),
                    evaluated_at=evaluated_at_by_zone.get(zone_id),
                )
            )

        freshness = power_health.freshness_seconds(latest_at)
        if freshness is None:
            sim_status = "idle"
        elif freshness <= 12:
            sim_status = "running"
        elif freshness <= 60:
            sim_status = "slow"
        else:
            sim_status = "stale"

        return PowerDashboardOut(
            overall_health=overall_health,
            overall_condition=overall_condition,
            critical_assets=critical_count,
            active_alerts=alert_count,
            zone_summary=zone_summary,
            asset_count=len(assets),
            utility="power",
            last_telemetry_at=latest_at,
            live_telemetry_points=len(latest),
            data_freshness_seconds=round(freshness, 1) if freshness is not None else None,
            simulator_status=sim_status,
            backend_status="online",
            api_status="healthy" if latest else "insufficient_data",
        )

    def gas_dashboard(self) -> GasDashboardOut:
        """
        Gas dashboard from live telemetry + rule-based condition assessment.

        Exposes existing Phase 15.1 Gas condition capability for the Command Center.
        Does not invent KPIs when telemetry is missing.
        """
        assets = self._assets.list(utility_kind="gas")
        zones = {z.id: z for z in self._zones.list()}
        latest = self._telemetry.latest_rows(utility_kind="gas")

        metrics_by_asset: dict[str, dict[str, float]] = defaultdict(dict)
        latest_at: datetime | None = None
        for asset_id, _name, tel in latest:
            metrics_by_asset[asset_id][tel.metric_key] = float(tel.value)
            if latest_at is None or tel.recorded_at > latest_at:
                latest_at = tel.recorded_at

        asset_health: dict[str, float] = {}
        asset_condition: dict[str, str] = {}
        asset_drivers: dict[str, list[str]] = {}
        asset_alert_counts: dict[str, int] = {}
        critical_count = 0
        alert_count = 0
        evaluated_at_by_zone: dict[str, datetime] = {}

        for asset in assets:
            metrics = metrics_by_asset.get(asset.id, {})
            profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
            nominals = {
                key: float(profile[key])
                for key in (
                    "nominal_pressure_bar",
                    "nominal_flow_nm3_h",
                    "nominal_discharge_pressure_bar",
                    "nominal_power_kw",
                )
                if isinstance(profile.get(key), (int, float))
            }

            stored_condition = profile.get("condition")
            stored_codes = profile.get("condition_alert_codes")
            stored_drivers = profile.get("condition_drivers")
            if metrics and isinstance(stored_condition, str) and stored_condition:
                condition = stored_condition
                alerts = (
                    [str(c) for c in stored_codes]
                    if isinstance(stored_codes, list)
                    else gas_health.evaluate_alerts(
                        metrics,
                        asset_type=asset.type,
                        nominals=nominals,
                    )
                )
                drivers = (
                    [str(d) for d in stored_drivers]
                    if isinstance(stored_drivers, list)
                    else gas_health.drivers_from_alerts(alerts)
                )
                blended = float(asset.health)
            elif metrics:
                result = gas_health.evaluate_asset_condition(
                    asset_id=asset.id,
                    zone_id=asset.zone_id,
                    asset_type=asset.type,
                    metrics=metrics,
                    nominals=nominals,
                    previous_health=float(asset.health),
                )
                condition = result.condition
                alerts = result.alert_codes
                drivers = result.drivers
                blended = result.health_score
            else:
                condition = "unavailable"
                alerts = []
                drivers = []
                blended = float(asset.health)

            asset_health[asset.id] = blended
            asset_condition[asset.id] = condition
            asset_drivers[asset.id] = drivers
            asset_alert_counts[asset.id] = len(alerts)
            alert_count += len(alerts)
            if _gas_is_critical_asset(
                criticality=asset.criticality,
                condition=condition,
                alerts=alerts,
                health=blended,
            ):
                critical_count += 1
            if condition != "unavailable":
                evaluated_at_by_zone[asset.zone_id] = datetime.now(timezone.utc)

        scored = [h for aid, h in asset_health.items() if asset_condition.get(aid) != "unavailable"]
        overall_health = round(sum(scored) / len(scored), 1) if scored else 0.0
        overall_condition = _aggregate_condition(list(asset_condition.values()))

        assets_by_zone: dict[str, list] = defaultdict(list)
        for a in assets:
            assets_by_zone[a.zone_id].append(a)

        # Stable A→D order for Command Center cards.
        zone_order = ("zone-a", "zone-b", "zone-c", "zone-d")
        ordered_zone_ids = [zid for zid in zone_order if zid in assets_by_zone]
        ordered_zone_ids.extend(
            sorted(zid for zid in assets_by_zone if zid not in zone_order)
        )

        zone_summary: list[ZoneGasSummaryOut] = []
        for zone_id in ordered_zone_ids:
            zone_assets = assets_by_zone[zone_id]
            zone = zones.get(zone_id)
            conditions = [asset_condition[a.id] for a in zone_assets]
            health_vals = [asset_health[a.id] for a in zone_assets]
            affected = [
                a.id
                for a in zone_assets
                if asset_condition[a.id] in {"warning", "high_risk", "critical"}
            ]
            driver_counts: dict[str, int] = defaultdict(int)
            for a in zone_assets:
                for d in asset_drivers.get(a.id, []):
                    driver_counts[d] += 1
            major_drivers = [
                d
                for d, _ in sorted(driver_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            ]
            pressures = [
                metrics_by_asset[a.id]["pressure_bar"]
                for a in zone_assets
                if "pressure_bar" in metrics_by_asset.get(a.id, {})
            ]
            flows = [
                metrics_by_asset[a.id]["flow_rate_nm3_h"]
                for a in zone_assets
                if "flow_rate_nm3_h" in metrics_by_asset.get(a.id, {})
            ]
            zone_summary.append(
                ZoneGasSummaryOut(
                    zone_id=zone_id,
                    zone_name=zone.name if zone else zone_id,
                    asset_count=len(zone_assets),
                    overall_condition=_aggregate_condition(conditions),
                    average_health=round(sum(health_vals) / len(health_vals), 1)
                    if health_vals
                    else 0.0,
                    affected_assets=affected,
                    major_drivers=major_drivers,
                    active_alert_count=sum(
                        asset_alert_counts.get(a.id, 0) for a in zone_assets
                    ),
                    average_pressure_bar=round(sum(pressures) / len(pressures), 3)
                    if pressures
                    else None,
                    average_flow_nm3_h=round(sum(flows) / len(flows), 2)
                    if flows
                    else None,
                    evaluated_at=evaluated_at_by_zone.get(zone_id),
                )
            )

        freshness = power_health.freshness_seconds(latest_at)
        if freshness is None:
            sim_status = "idle"
        elif freshness <= 12:
            sim_status = "running"
        elif freshness <= 60:
            sim_status = "slow"
        else:
            sim_status = "stale"

        return GasDashboardOut(
            overall_health=overall_health,
            overall_condition=overall_condition,
            critical_assets=critical_count,
            active_alerts=alert_count,
            zone_summary=zone_summary,
            asset_count=len(assets),
            utility="gas",
            last_telemetry_at=latest_at,
            live_telemetry_points=len(latest),
            data_freshness_seconds=round(freshness, 1) if freshness is not None else None,
            simulator_status=sim_status,
            backend_status="online",
            api_status="healthy" if latest else "insufficient_data",
        )

    def communications_dashboard(self) -> CommunicationsDashboardOut:
        """
        Communications dashboard from live telemetry + rule-based condition.

        Uses seeded Comm assets only — no fabricated KPIs or asset counts.
        """
        assets = self._assets.list(utility_kind="communications")
        zones = {z.id: z for z in self._zones.list()}
        latest = self._telemetry.latest_rows(utility_kind="communications")

        metrics_by_asset: dict[str, dict[str, float]] = defaultdict(dict)
        latest_at: datetime | None = None
        for asset_id, _name, tel in latest:
            metrics_by_asset[asset_id][tel.metric_key] = float(tel.value)
            if latest_at is None or tel.recorded_at > latest_at:
                latest_at = tel.recorded_at

        asset_health: dict[str, float] = {}
        asset_condition: dict[str, str] = {}
        asset_drivers: dict[str, list[str]] = {}
        asset_alert_counts: dict[str, int] = {}
        critical_count = 0
        alert_count = 0
        evaluated_at_by_zone: dict[str, datetime] = {}

        for asset in assets:
            metrics = metrics_by_asset.get(asset.id, {})
            profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
            nominals = {
                key: float(profile[key])
                for key in (
                    "nominal_link_rtt_ms",
                    "nominal_packet_loss_pct",
                    "nominal_cabinet_temp_c",
                    "nominal_power_draw_w",
                )
                if isinstance(profile.get(key), (int, float))
            }

            stored_condition = profile.get("condition")
            stored_codes = profile.get("condition_alert_codes")
            stored_drivers = profile.get("condition_drivers")
            if metrics and isinstance(stored_condition, str) and stored_condition:
                condition = stored_condition
                alerts = (
                    [str(c) for c in stored_codes]
                    if isinstance(stored_codes, list)
                    else communications_health.evaluate_alerts(
                        metrics,
                        asset_type=asset.type,
                        nominals=nominals,
                    )
                )
                drivers = (
                    [str(d) for d in stored_drivers]
                    if isinstance(stored_drivers, list)
                    else communications_health.drivers_from_alerts(alerts)
                )
                blended = float(asset.health)
            elif metrics:
                result = communications_health.evaluate_asset_condition(
                    asset_id=asset.id,
                    zone_id=asset.zone_id,
                    asset_type=asset.type,
                    metrics=metrics,
                    nominals=nominals,
                    previous_health=float(asset.health),
                )
                condition = result.condition
                alerts = result.alert_codes
                drivers = result.drivers
                blended = result.health_score
            else:
                condition = "unavailable"
                alerts = []
                drivers = []
                blended = float(asset.health)

            asset_health[asset.id] = blended
            asset_condition[asset.id] = condition
            asset_drivers[asset.id] = drivers
            asset_alert_counts[asset.id] = len(alerts)
            alert_count += len(alerts)
            if _communications_is_critical_asset(
                criticality=asset.criticality,
                condition=condition,
                alerts=alerts,
                health=blended,
            ):
                critical_count += 1
            if condition != "unavailable":
                evaluated_at_by_zone[asset.zone_id] = datetime.now(timezone.utc)

        scored = [
            h for aid, h in asset_health.items() if asset_condition.get(aid) != "unavailable"
        ]
        overall_health = round(sum(scored) / len(scored), 1) if scored else 0.0
        overall_condition = _aggregate_condition(list(asset_condition.values()))

        assets_by_zone: dict[str, list] = defaultdict(list)
        for a in assets:
            assets_by_zone[a.zone_id].append(a)

        zone_order = ("zone-a", "zone-b", "zone-c", "zone-d")
        ordered_zone_ids = [zid for zid in zone_order if zid in assets_by_zone]
        ordered_zone_ids.extend(
            sorted(zid for zid in assets_by_zone if zid not in zone_order)
        )

        zone_summary: list[ZoneCommunicationsSummaryOut] = []
        for zone_id in ordered_zone_ids:
            zone_assets = assets_by_zone[zone_id]
            zone = zones.get(zone_id)
            conditions = [asset_condition[a.id] for a in zone_assets]
            health_vals = [asset_health[a.id] for a in zone_assets]
            affected = [
                a.id
                for a in zone_assets
                if asset_condition[a.id] in {"warning", "high_risk", "critical", "unavailable"}
            ]
            driver_counts: dict[str, int] = defaultdict(int)
            for a in zone_assets:
                for d in asset_drivers.get(a.id, []):
                    driver_counts[d] += 1
            major_drivers = [
                d
                for d, _ in sorted(driver_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            ]
            zone_summary.append(
                ZoneCommunicationsSummaryOut(
                    zone_id=zone_id,
                    zone_name=zone.name if zone else zone_id,
                    asset_count=len(zone_assets),
                    overall_condition=_aggregate_condition(conditions),
                    average_health=round(sum(health_vals) / len(health_vals), 1)
                    if health_vals
                    else 0.0,
                    affected_assets=affected,
                    major_drivers=major_drivers,
                    active_alert_count=sum(
                        asset_alert_counts.get(a.id, 0) for a in zone_assets
                    ),
                    evaluated_at=evaluated_at_by_zone.get(zone_id),
                )
            )

        freshness = power_health.freshness_seconds(latest_at)
        if freshness is None:
            sim_status = "idle"
        elif freshness <= 12:
            sim_status = "running"
        elif freshness <= 60:
            sim_status = "slow"
        else:
            sim_status = "stale"

        return CommunicationsDashboardOut(
            overall_health=overall_health,
            overall_condition=overall_condition,
            critical_assets=critical_count,
            active_alerts=alert_count,
            zone_summary=zone_summary,
            asset_count=len(assets),
            utility="communications",
            last_telemetry_at=latest_at,
            live_telemetry_points=len(latest),
            data_freshness_seconds=round(freshness, 1) if freshness is not None else None,
            simulator_status=sim_status,
            backend_status="online",
            api_status="healthy" if latest else "insufficient_data",
        )

    def corridor_dashboard(self) -> CorridorDashboardOut:
        """
        Compose Overall Corridor operational posture from live Water + Power + Gas
        + Communications dashboards (composition only — no new health model).

        Does not invent a scientific corridor health percentage.
        """
        water = self.water_dashboard()
        power = self.power_dashboard()
        gas = self.gas_dashboard()
        communications = self.communications_dashboard()
        now = datetime.now(timezone.utc)

        water_posture = water_signals_to_posture(
            overall_health=float(water.overall_health),
            critical_assets=int(water.critical_assets),
            active_alerts=int(water.active_alerts),
            api_status=str(water.api_status),
        )
        power_posture = power_condition_to_posture(
            power.overall_condition,
            api_status=str(power.api_status),
        )
        gas_posture = gas_condition_to_posture(
            gas.overall_condition,
            api_status=str(gas.api_status),
        )
        communications_posture = communications_condition_to_posture(
            communications.overall_condition,
            api_status=str(communications.api_status),
        )

        utilities = [
            CorridorUtilityPostureOut(
                utility="water",
                available=water_posture != "UNAVAILABLE",
                posture=water_posture,
                overall_health=water.overall_health,
                overall_condition=None,
                asset_count=water.asset_count,
                critical_assets=water.critical_assets,
                active_alerts=water.active_alerts,
                api_status=water.api_status,
            ),
            CorridorUtilityPostureOut(
                utility="power",
                available=power_posture != "UNAVAILABLE",
                posture=power_posture,
                overall_health=power.overall_health,
                overall_condition=power.overall_condition,
                asset_count=power.asset_count,
                critical_assets=power.critical_assets,
                active_alerts=power.active_alerts,
                api_status=power.api_status,
            ),
            CorridorUtilityPostureOut(
                utility="gas",
                available=gas_posture != "UNAVAILABLE",
                posture=gas_posture,
                overall_health=gas.overall_health,
                overall_condition=gas.overall_condition,
                asset_count=gas.asset_count,
                critical_assets=gas.critical_assets,
                active_alerts=gas.active_alerts,
                api_status=gas.api_status,
            ),
            CorridorUtilityPostureOut(
                utility="communications",
                available=communications_posture != "UNAVAILABLE",
                posture=communications_posture,
                overall_health=communications.overall_health,
                overall_condition=communications.overall_condition,
                asset_count=communications.asset_count,
                critical_assets=communications.critical_assets,
                active_alerts=communications.active_alerts,
                api_status=communications.api_status,
            ),
        ]

        overall = compose_corridor_posture(
            [water_posture, power_posture, gas_posture, communications_posture]
        )
        drivers: list[str] = []
        if water_posture in {"STABLE", "DEGRADED", "CRITICAL"}:
            drivers.append(
                f"Water posture {water_posture} "
                f"(health {water.overall_health:.1f}%, "
                f"{water.critical_assets} critical assets, "
                f"{water.active_alerts} threshold alerts)"
            )
        if power_posture in {"STABLE", "DEGRADED", "CRITICAL"}:
            drivers.append(
                f"Power posture {power_posture} "
                f"(condition {power.overall_condition}, "
                f"health {power.overall_health:.1f}%, "
                f"{power.active_alerts} threshold alerts)"
            )
        if gas_posture in {"STABLE", "DEGRADED", "CRITICAL"}:
            drivers.append(
                f"Gas posture {gas_posture} "
                f"(condition {gas.overall_condition}, "
                f"health {gas.overall_health:.1f}%, "
                f"{gas.active_alerts} threshold alerts)"
            )
        if communications_posture in {"STABLE", "DEGRADED", "CRITICAL"}:
            drivers.append(
                f"Communications posture {communications_posture} "
                f"(condition {communications.overall_condition}, "
                f"health {communications.overall_health:.1f}%, "
                f"{communications.active_alerts} threshold alerts)"
            )
        if not drivers:
            drivers.append(
                "Water, Power, Gas, and Communications operational signals are "
                "within healthy thresholds."
            )

        explanation = (
            f"Overall corridor posture is {overall}, composed as the worst of live "
            f"Water ({water_posture}), Power ({power_posture}), Gas ({gas_posture}), "
            f"and Communications ({communications_posture}) utility postures. "
            "This is a categorical operational posture from existing dashboards — "
            "not a fabricated scientific corridor health index."
        )

        # Phase 16.6 — cross-utility dependency exposure (evidence only; posture unchanged).
        dependency_exposure = self._corridor_dependency_exposure()
        situation_summary = self._corridor_situation_summary(
            overall_posture=overall,
            utilities=utilities,
            exposure=dependency_exposure,
            base_explanation=explanation,
        )

        return CorridorDashboardOut(
            overall_posture=overall,
            explanation=explanation,
            drivers=drivers,
            utilities=utilities,
            total_assets=(
                water.asset_count
                + power.asset_count
                + gas.asset_count
                + communications.asset_count
            ),
            total_critical_assets=(
                water.critical_assets
                + power.critical_assets
                + gas.critical_assets
                + communications.critical_assets
            ),
            total_active_alerts=(
                water.active_alerts
                + power.active_alerts
                + gas.active_alerts
                + communications.active_alerts
            ),
            evaluated_at=now,
            dependency_exposure=dependency_exposure,
            situation_summary=situation_summary,
        )

    def _corridor_dependency_exposure(self) -> CorridorDependencyExposureOut:
        """
        Aggregate CrossUtilityImpactService for live critical/unavailable sources.

        Does not elevate corridor posture. One call per qualifying source.
        """
        from app.domain.enums import DependencyType
        from app.domain.repositories import AssetDependencyRepository
        from app.services.cross_utility_impact_service import CrossUtilityImpactService

        empty = CorridorDependencyExposureOut(available=False)
        edges = AssetDependencyRepository(self._db).list(
            dependency_type=DependencyType.SUPPLIES.value,
            enabled=True,
        )
        source_ids = sorted({e.source_asset_id for e in edges})
        if not source_ids:
            return empty

        impact_svc = CrossUtilityImpactService(self._db)
        sources: list[CorridorDependencySourceOut] = []
        affected_utilities: set[str] = set()
        affected_zones: set[str] = set()
        affected_asset_ids: set[str] = set()
        all_paths: list[CorridorDependencyPathOut] = []
        explanation_lines: list[str] = []

        for source_id in source_ids:
            asset = self._assets.get(source_id)
            if asset is None:
                continue
            trigger = self._resolve_live_dependency_trigger(asset)
            if trigger is None:
                continue

            assessment = impact_svc.assess_asset_impact(
                asset_id=source_id,
                condition=trigger,
            )
            if int(assessment.get("affected_asset_count") or 0) <= 0:
                continue

            affected_rows = list(assessment.get("affected_assets") or [])
            utils = [str(u) for u in (assessment.get("affected_utilities") or [])]
            zones = [str(z) for z in (assessment.get("affected_zones") or [])]
            paths_raw = list(assessment.get("dependency_paths") or [])
            affected_utilities.update(utils)
            affected_zones.update(zones)

            affected_out: list[CorridorDependencyAffectedAssetOut] = []
            for row in affected_rows:
                aid = str(row.get("asset_id") or "")
                if not aid:
                    continue
                affected_asset_ids.add(aid)
                affected_out.append(
                    CorridorDependencyAffectedAssetOut(
                        asset_id=aid,
                        asset_name=str(row.get("asset_name") or ""),
                        utility=str(row.get("utility") or ""),
                        zone=str(row.get("zone") or ""),
                        impact_kind=str(row.get("impact_kind") or ""),
                        depth=int(row.get("depth") or 0),
                        posture=str(row.get("posture") or "potentially_affected"),
                    )
                )

            path_out = [
                CorridorDependencyPathOut(
                    asset_id=str(p.get("asset_id") or ""),
                    depth=int(p.get("depth") or 0),
                    impact_kind=str(p.get("impact_kind") or ""),
                    path_asset_ids=list(p.get("path_asset_ids") or []),
                    path_dependency_ids=list(p.get("path_dependency_ids") or []),
                )
                for p in paths_raw
            ]
            all_paths.extend(path_out)

            source_util = str(assessment.get("source_utility") or asset.utility_kind)
            source_zone = str(assessment.get("source_zone") or asset.zone_id)
            util_label = source_util.capitalize() if source_util else "Utility"
            dependents_txt = ", ".join(
                f"{(a.utility or '').capitalize()} {a.asset_id} ({a.zone})"
                for a in affected_out
            )
            explanation_lines.append(
                f"{util_label} asset {source_id} is {trigger} with modeled downstream "
                f"dependency exposure on {dependents_txt}. "
                "Potential/hypothetical exposure only — not a declared multi-utility outage."
            )
            sources.append(
                CorridorDependencySourceOut(
                    source_asset_id=source_id,
                    source_utility=source_util,
                    source_zone=source_zone,
                    condition=trigger,
                    affected_assets=affected_out,
                    affected_utilities=utils,
                    affected_zones=zones,
                    dependency_paths=path_out,
                    hypothetical=True,
                    assessment_kind=str(
                        assessment.get("assessment_kind") or "explicit_dependency_impact"
                    ),
                )
            )

        if not sources:
            return empty

        return CorridorDependencyExposureOut(
            available=True,
            hypothetical=True,
            contributes_to_overall_posture=False,
            source_asset_ids=[s.source_asset_id for s in sources],
            affected_asset_ids=sorted(affected_asset_ids),
            affected_utilities=sorted(affected_utilities),
            affected_zones=sorted(affected_zones),
            sources=sources,
            dependency_paths=all_paths,
            explanation=" ".join(explanation_lines),
        )

    @classmethod
    def _resolve_live_dependency_trigger(cls, asset: Any) -> str | None:
        """Same live trigger semantics as Phase 16.2 / 16.5."""
        profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
        raw = str(profile.get("condition") or "").strip().lower()
        raw = raw.replace("-", "_").replace(" ", "_")
        if raw in {"offline", "failed"}:
            raw = "unavailable"
        if raw in cls._DEPENDENCY_TRIGGERS:
            return raw
        status = str(getattr(asset, "status", "") or "").strip().lower()
        if status in {"offline", "unavailable", "failed", "out_of_service"}:
            return "unavailable"
        return None

    @staticmethod
    def _zone_display(zone_id: str) -> str:
        raw = (zone_id or "").strip()
        if not raw:
            return "the corridor"
        if raw.lower().startswith("zone-"):
            suffix = raw.split("-", 1)[-1].upper()
            return f"Zone {suffix}"
        return raw

    @staticmethod
    def _utility_display(utility: str) -> str:
        raw = (utility or "").strip().lower()
        return {
            "power": "Power",
            "water": "Water",
            "gas": "Gas",
        }.get(raw, raw.capitalize() if raw else "Utility")

    @classmethod
    def _corridor_situation_summary(
        cls,
        *,
        overall_posture: str,
        utilities: list[CorridorUtilityPostureOut],
        exposure: CorridorDependencyExposureOut,
        base_explanation: str,
    ) -> str:
        """Deterministic corridor situation text from live posture + exposure evidence."""
        if not exposure.available or not exposure.sources:
            if overall_posture in {"HEALTHY", "STABLE"}:
                return (
                    f"Corridor operational posture is {overall_posture}. "
                    "No modeled cross-utility dependency exposure from live "
                    "critical or unavailable sources."
                )
            return (
                f"{base_explanation} No modeled cross-utility dependency exposure "
                "from live critical or unavailable sources."
            )

        # Primary operational concern = worst available utility posture.
        ranked = sorted(
            [u for u in utilities if u.available],
            key=lambda u: _POSTURE_RANK.get(u.posture, 0),
            reverse=True,
        )
        primary = ranked[0] if ranked else None

        # Prefer the exposure source that matches the primary utility when possible.
        primary_src = exposure.sources[0]
        if primary is not None:
            match = next(
                (
                    s
                    for s in exposure.sources
                    if (s.source_utility or "").lower() == primary.utility.lower()
                ),
                None,
            )
            if match is not None:
                primary_src = match

        util_label = cls._utility_display(primary_src.source_utility)
        zone_label = cls._zone_display(primary_src.source_zone)
        dependent_phrases: list[str] = []
        for src in exposure.sources:
            for row in src.affected_assets:
                name = (row.asset_name or row.asset_id).strip()
                dep_util = cls._utility_display(row.utility)
                # Prefer readable "Water Pump …" when name already includes type.
                if name and name.lower() != row.asset_id.lower():
                    dependent_phrases.append(f"{dep_util} {name} ({row.asset_id})")
                else:
                    dependent_phrases.append(f"{dep_util} asset {row.asset_id}")

        # Stable order for deterministic summaries.
        dependent_phrases = sorted(set(dependent_phrases))
        if len(dependent_phrases) == 1:
            dependents_txt = dependent_phrases[0]
        elif len(dependent_phrases) == 2:
            dependents_txt = f"{dependent_phrases[0]} and {dependent_phrases[1]}"
        else:
            dependents_txt = (
                ", ".join(dependent_phrases[:-1]) + f", and {dependent_phrases[-1]}"
            )

        return (
            f"{util_label} is the primary operational concern in {zone_label}, "
            f"with modeled downstream exposure to {dependents_txt}. "
            "Potential/hypothetical dependency exposure only — not a declared "
            "multi-utility outage."
        )
