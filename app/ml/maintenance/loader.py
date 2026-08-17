"""Load real pump-station telemetry from PostgreSQL for PdM feature prep."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.repositories import AssetRepository
from app.domain.repositories.scenario_repository import ScenarioRepository
from app.domain.repositories.telemetry_repository import TelemetryRepository
from app.ml.common.constants import MAINTENANCE_METRIC_KEYS


def load_maintenance_telemetry(
    db: Session,
    *,
    asset_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50_000,
) -> list[dict[str, Any]]:
    """
    Fetch pump-related telemetry chronologically.

    When ``asset_id`` is omitted, loads history for all ``pump_station`` assets.
    """
    repo = TelemetryRepository(db)
    assets = AssetRepository(db)

    if asset_id:
        return repo.history_rows(
            asset_id=asset_id,
            metric_keys=list(MAINTENANCE_METRIC_KEYS),
            start=start,
            end=end,
            limit=limit,
        )

    pump_ids = [a.id for a in assets.list(utility_kind="water") if a.type == "pump_station"]
    if not pump_ids:
        return []

    return repo.history_rows(
        asset_ids=pump_ids,
        metric_keys=list(MAINTENANCE_METRIC_KEYS),
        start=start,
        end=end,
        limit=limit,
    )


def load_asset_context(db: Session, asset_id: str) -> dict[str, Any] | None:
    """
    Persisted asset attributes joined into maintenance feature frames.

    Scenario state currently stores only the active scenario (not historical
    transitions), so ``scenario_intensity`` is the latest available context.
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
