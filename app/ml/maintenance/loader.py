"""Load real pump-station telemetry from PostgreSQL for PdM feature prep."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.repositories import AssetRepository
from app.domain.repositories.scenario_repository import ScenarioRepository
from app.domain.repositories.telemetry_repository import TelemetryRepository
from app.ml.common.constants import (
    INFERENCE_HISTORY_ROWS_PER_ASSET,
    MAINTENANCE_METRIC_KEYS,
)


def load_maintenance_telemetry(
    db: Session,
    *,
    asset_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50_000,
) -> list[dict[str, Any]]:
    """
    Fetch pump-related telemetry chronologically (newest window per asset).

    When ``asset_id`` is omitted, loads a recent history window for each Water
    ``pump_station`` independently so one asset cannot starve others under a
    global LIMIT. Uses the shared ``history_rows`` newest-window CTE.
    """
    repo = TelemetryRepository(db)
    assets = AssetRepository(db)

    if asset_id:
        asset = assets.get(asset_id)
        if asset is None:
            return []
        if asset.utility_kind != "water" or asset.type != "pump_station":
            return []
        return repo.history_rows(
            asset_id=asset_id,
            metric_keys=list(MAINTENANCE_METRIC_KEYS),
            start=start,
            end=end,
            limit=limit,
        )

    pump_ids = [
        a.id for a in assets.list(utility_kind="water") if a.type == "pump_station"
    ]
    if not pump_ids:
        return []

    per_asset = max(
        INFERENCE_HISTORY_ROWS_PER_ASSET,
        limit // max(1, len(pump_ids)),
    )
    rows: list[dict[str, Any]] = []
    for pump_id in pump_ids:
        rows.extend(
            repo.history_rows(
                asset_id=pump_id,
                metric_keys=list(MAINTENANCE_METRIC_KEYS),
                start=start,
                end=end,
                limit=per_asset,
            )
        )
    return rows


def load_asset_context(db: Session, asset_id: str) -> dict[str, Any] | None:
    """
    Persisted asset attributes joined into maintenance feature frames.

    ``health`` / ``status`` / ``zone`` / ``scenario_intensity`` remain available
    for deterministic labeling and API context. They are NOT model inputs
    (see ``NUMERIC_FEATURES`` / ``CATEGORICAL_FEATURES`` in xgboost_model).
    """
    asset = AssetRepository(db).get(asset_id)
    if asset is None:
        return None
    scenario = ScenarioRepository(db).get_or_create()
    return {
        "asset_id": asset.id,
        "asset_name": asset.name,
        "health": float(asset.health),
        "status": asset.status,
        "zone": asset.zone_id,
        "criticality": asset.criticality,
        "asset_type": asset.type,
        "scenario_intensity": float(scenario.intensity),
    }
