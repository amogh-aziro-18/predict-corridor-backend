"""Load Gas telemetry from PostgreSQL for PdM feature prep."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.repositories import AssetRepository
from app.domain.repositories.telemetry_repository import TelemetryRepository
from app.ml.gas.constants import (
    GAS_ASSET_TYPES,
    GAS_METRIC_KEYS,
    INFERENCE_HISTORY_ROWS_PER_ASSET,
)


def load_gas_telemetry(
    db: Session,
    *,
    asset_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50_000,
) -> list[dict[str, Any]]:
    """
    Fetch Gas telemetry chronologically (newest window per asset).

    When ``asset_id`` is omitted, loads a recent history window for each Gas
    asset independently so one asset cannot starve others under a global LIMIT.
    """
    repo = TelemetryRepository(db)
    assets = AssetRepository(db)

    if asset_id:
        asset = assets.get(asset_id)
        if asset is None:
            return []
        if asset.utility_kind != "gas" or asset.type not in GAS_ASSET_TYPES:
            return []
        return repo.history_rows(
            asset_id=asset_id,
            metric_keys=list(GAS_METRIC_KEYS),
            start=start,
            end=end,
            limit=limit,
        )

    gas_ids = [
        a.id for a in assets.list(utility_kind="gas") if a.type in GAS_ASSET_TYPES
    ]
    if not gas_ids:
        return []

    per_asset = max(
        INFERENCE_HISTORY_ROWS_PER_ASSET,
        limit // max(1, len(gas_ids)),
    )
    rows: list[dict[str, Any]] = []
    for gid in gas_ids:
        rows.extend(
            repo.history_rows(
                asset_id=gid,
                metric_keys=list(GAS_METRIC_KEYS),
                start=start,
                end=end,
                limit=per_asset,
            )
        )
    return rows


def load_gas_context(db: Session, asset_id: str) -> dict[str, Any] | None:
    """Persisted Gas attributes for feature frames (not labels / not zone shortcuts)."""
    asset = AssetRepository(db).get(asset_id)
    if asset is None:
        return None
    if asset.utility_kind != "gas" or asset.type not in GAS_ASSET_TYPES:
        return None
    profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}

    def _nom(key: str, default: float) -> float:
        raw = profile.get(key)
        return float(raw) if isinstance(raw, (int, float)) else default

    return {
        "asset_id": asset.id,
        "asset_name": asset.name,
        "zone": asset.zone_id,
        "criticality": asset.criticality,
        "asset_type": asset.type,
        "utility_kind": asset.utility_kind,
        "nominal_pressure_bar": _nom("nominal_pressure_bar", 28.0),
        "nominal_flow_nm3_h": _nom("nominal_flow_nm3_h", 2000.0),
        "nominal_discharge_pressure_bar": _nom("nominal_discharge_pressure_bar", 32.0),
        "nominal_power_kw": _nom("nominal_power_kw", 380.0),
    }


__all__ = [
    "load_gas_context",
    "load_gas_telemetry",
]
