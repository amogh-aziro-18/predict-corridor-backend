"""Load Power active_power_mw telemetry from PostgreSQL for load forecasting."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.repositories import AssetRepository
from app.domain.repositories.telemetry_repository import TelemetryRepository
from app.ml.power.forecast_constants import POWER_LOAD_METRIC_KEY


def resolve_power_load_asset_ids(
    db: Session,
    *,
    zone_id: str | None = None,
) -> list[str]:
    """All Power assets that can emit active_power_mw (substation/transformer/feeder)."""
    assets = AssetRepository(db).list(utility_kind="power", zone_id=zone_id)
    return [
        a.id
        for a in assets
        if a.type in {"substation", "transformer", "feeder"}
    ]


def load_power_load_telemetry(
    db: Session,
    *,
    zone_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100_000,
) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Fetch chronological ``active_power_mw`` rows for Power assets only.

    Never loads Water telemetry.
    """
    asset_ids = resolve_power_load_asset_ids(db, zone_id=zone_id)
    if not asset_ids:
        return [], []

    rows = TelemetryRepository(db).history_rows(
        asset_ids=asset_ids,
        metric_keys=[POWER_LOAD_METRIC_KEY],
        start=start,
        end=end,
        limit=limit,
    )
    return asset_ids, rows


__all__ = [
    "load_power_load_telemetry",
    "resolve_power_load_asset_ids",
]
