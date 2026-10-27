"""Load Gas flow_rate_nm3_h telemetry from PostgreSQL for demand forecasting."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.repositories import AssetRepository
from app.domain.repositories.telemetry_repository import TelemetryRepository
from app.ml.gas.forecast_constants import (
    GAS_DEMAND_ASSET_TYPE_PRIORITY,
    GAS_DEMAND_METRIC_KEY,
)


def resolve_gas_demand_asset_ids(
    db: Session,
    *,
    zone_id: str | None = None,
) -> tuple[str, list[str]]:
    """
    Prefer a single network layer so CMP+REG+GFDR flows are not triple-counted::

        gas_feeder → gas_regulator_station → gas_compressor
    """
    assets = AssetRepository(db).list(utility_kind="gas", zone_id=zone_id)
    by_type: dict[str, list[str]] = {}
    for asset in assets:
        by_type.setdefault(asset.type, []).append(asset.id)

    for asset_type in GAS_DEMAND_ASSET_TYPE_PRIORITY:
        ids = by_type.get(asset_type) or []
        if ids:
            return asset_type, ids

    return "none", []


def load_gas_demand_telemetry(
    db: Session,
    *,
    zone_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100_000,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """
    Fetch chronological ``flow_rate_nm3_h`` rows for preferred Gas demand assets.

    Never loads Water/Power telemetry. Does not use zone/scenario/health labels
    as series values.
    """
    asset_type, asset_ids = resolve_gas_demand_asset_ids(db, zone_id=zone_id)
    if not asset_ids:
        return asset_type, [], []

    rows = TelemetryRepository(db).history_rows(
        asset_ids=asset_ids,
        metric_keys=[GAS_DEMAND_METRIC_KEY],
        start=start,
        end=end,
        limit=limit,
    )
    return asset_type, asset_ids, rows


__all__ = [
    "load_gas_demand_telemetry",
    "resolve_gas_demand_asset_ids",
]
