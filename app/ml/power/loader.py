"""Load transformer Power telemetry from PostgreSQL for PdM feature prep."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.repositories import AssetRepository
from app.domain.repositories.telemetry_repository import TelemetryRepository
from app.ml.power.constants import (
    INFERENCE_HISTORY_ROWS_PER_ASSET,
    TRANSFORMER_ASSET_TYPE,
    TRANSFORMER_METRIC_KEYS,
)


def load_transformer_telemetry(
    db: Session,
    *,
    asset_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50_000,
) -> list[dict[str, Any]]:
    """
    Fetch Power transformer telemetry chronologically (newest window per asset).

    When ``asset_id`` is omitted, loads a recent history window for each Power
    transformer independently so one asset cannot starve others under a global
    LIMIT. Never loads Water assets.
    """
    repo = TelemetryRepository(db)
    assets = AssetRepository(db)

    if asset_id:
        asset = assets.get(asset_id)
        if asset is None:
            return []
        if asset.utility_kind != "power" or asset.type != TRANSFORMER_ASSET_TYPE:
            return []
        return repo.history_rows(
            asset_id=asset_id,
            metric_keys=list(TRANSFORMER_METRIC_KEYS),
            start=start,
            end=end,
            limit=limit,
        )

    xfmr_ids = [
        a.id
        for a in assets.list(utility_kind="power")
        if a.type == TRANSFORMER_ASSET_TYPE
    ]
    if not xfmr_ids:
        return []

    per_asset = max(
        INFERENCE_HISTORY_ROWS_PER_ASSET,
        limit // max(1, len(xfmr_ids)),
    )
    rows: list[dict[str, Any]] = []
    for xfmr_id in xfmr_ids:
        rows.extend(
            repo.history_rows(
                asset_id=xfmr_id,
                metric_keys=list(TRANSFORMER_METRIC_KEYS),
                start=start,
                end=end,
                limit=per_asset,
            )
        )
    return rows


def load_transformer_context(db: Session, asset_id: str) -> dict[str, Any] | None:
    """Persisted Power transformer attributes for feature frames (not labels)."""
    asset = AssetRepository(db).get(asset_id)
    if asset is None:
        return None
    if asset.utility_kind != "power" or asset.type != TRANSFORMER_ASSET_TYPE:
        return None
    profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
    nominal = profile.get("nominal_voltage_kv")
    return {
        "asset_id": asset.id,
        "asset_name": asset.name,
        "zone": asset.zone_id,
        "criticality": asset.criticality,
        "asset_type": asset.type,
        "utility_kind": asset.utility_kind,
        "nominal_voltage_kv": float(nominal)
        if isinstance(nominal, (int, float))
        else 132.0,
    }


__all__ = [
    "load_transformer_context",
    "load_transformer_telemetry",
]
