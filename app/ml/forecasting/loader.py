"""Load real forecast-relevant telemetry from PostgreSQL (no mocks / no CSV)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.repositories import AssetRepository
from app.domain.repositories.telemetry_repository import TelemetryRepository
from app.ml.common.constants import (
    DEMAND_ASSET_TYPE_PRIORITY,
    DEMAND_METRIC_KEY,
    FORECAST_METRIC_KEYS,
)


def load_forecast_telemetry(
    db: Session,
    *,
    utility_kind: str = "water",
    metric_keys: tuple[str, ...] | list[str] | None = None,
    asset_id: str | None = None,
    asset_ids: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50_000,
) -> list[dict[str, Any]]:
    """
    Fetch chronological telemetry rows for forecasting metrics.

    Defaults to ``flow_rate``, ``pressure``, and ``reservoir_level`` across
    all water assets unless ``asset_id`` / ``metric_keys`` narrow the query.
    """
    keys = tuple(metric_keys) if metric_keys is not None else FORECAST_METRIC_KEYS
    repo = TelemetryRepository(db)
    return repo.history_rows(
        asset_id=asset_id,
        asset_ids=asset_ids,
        metric_keys=list(keys),
        utility_kind=utility_kind if asset_id is None and not asset_ids else None,
        start=start,
        end=end,
        limit=limit,
    )


def resolve_demand_asset_ids(
    db: Session,
    *,
    zone_id: str | None = None,
) -> tuple[str, list[str]]:
    """
    Pick demand source assets by priority::

        flow_meter → distribution_network → pump_station

    Returns ``(asset_type, asset_ids)`` for the first type that has assets
    (optionally filtered by zone).
    """
    assets = AssetRepository(db).list(utility_kind="water", zone_id=zone_id)
    by_type: dict[str, list[str]] = {}
    for asset in assets:
        by_type.setdefault(asset.type, []).append(asset.id)

    for asset_type in DEMAND_ASSET_TYPE_PRIORITY:
        ids = by_type.get(asset_type) or []
        if ids:
            return asset_type, ids

    flow_capable = [
        a.id
        for a in assets
        if a.type
        in {
            "flow_meter",
            "distribution_network",
            "pump_station",
            "water_treatment_plant",
        }
    ]
    return "mixed_flow", flow_capable


def load_demand_telemetry(
    db: Session,
    *,
    zone_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100_000,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Load ``flow_rate`` history for preferred demand assets from PostgreSQL."""
    asset_type, asset_ids = resolve_demand_asset_ids(db, zone_id=zone_id)
    if not asset_ids:
        return asset_type, [], []

    rows = load_forecast_telemetry(
        db,
        metric_keys=[DEMAND_METRIC_KEY],
        asset_ids=asset_ids,
        start=start,
        end=end,
        limit=limit,
    )
    return asset_type, asset_ids, rows
