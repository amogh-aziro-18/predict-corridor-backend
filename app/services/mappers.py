"""ORM → API DTO mappers."""

from __future__ import annotations

from app.api.schemas.asset import AssetOut
from app.api.schemas.corridor import CorridorOut
from app.api.schemas.utility import SeriesPointOut, UtilitySummaryOut
from app.api.schemas.zone import ZoneOut
from app.domain.models import Asset, Corridor, Utility, Zone


def zone_to_out(zone: Zone) -> ZoneOut:
    return ZoneOut(
        id=zone.id,
        name=zone.name,
        code=zone.code,
        area_sq_km=zone.area_sq_km,
        health=zone.health,
        population=zone.population,
        utilities=list(zone.utility_kinds or []),
        critical_assets=zone.critical_assets,
    )


def corridor_to_out(corridor: Corridor, zones: list[Zone]) -> CorridorOut:
    return CorridorOut(
        id=corridor.id,
        name=corridor.name,
        authority=corridor.authority,
        length_km=corridor.length_km,
        zones=[zone_to_out(z) for z in zones],
    )


def asset_to_out(asset: Asset) -> AssetOut:
    return AssetOut(
        id=asset.id,
        asset_id=asset.id,
        name=asset.name,
        asset_name=asset.name,
        zone=asset.zone_id,
        utility=asset.utility_kind,
        type=asset.type,
        latitude=asset.latitude,
        longitude=asset.longitude,
        status=asset.status,
        health=asset.health,
        health_score=asset.health,
        risk=asset.risk,
        # Static seed metadata — not live weather or flood intelligence.
        weather_exposure=asset.weather_exposure,
        flood_risk=asset.flood_risk,
        criticality=asset.criticality,
        backup=asset.backup,
        recovery_priority=asset.recovery_priority,
        last_maintenance=asset.last_maintenance,
        next_inspection=asset.next_inspection,
        predicted_failure_days=asset.predicted_failure_days,
        confidence=asset.confidence,
        profile=dict(asset.profile or {}),
    )


def utility_to_summary(
    utility: Utility,
    *,
    zones: list[str],
    asset_count: int,
    operational_health: float,
    series: list[SeriesPointOut] | None = None,
) -> UtilitySummaryOut:
    return UtilitySummaryOut(
        id=utility.id,
        kind=utility.kind,
        name=utility.name,
        zones=zones,
        operational_health=round(operational_health, 1),
        forecast_health=round(operational_health, 1),
        forecast_window="14 days",
        weather_impact="medium",
        disaster_risk="medium",
        sla_compliance=96.0,
        continuity=round(min(99.0, operational_health + 2), 1),
        recovery_priority=1 if utility.kind == "water" else 3,
        trend="flat",
        trend_delta=0.0,
        alerts=0,
        assets=asset_count,
        telemetry_coverage=94.0,
        data_freshness="seeded",
        series=series or [],
    )
