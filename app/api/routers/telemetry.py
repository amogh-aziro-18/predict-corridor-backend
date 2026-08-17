"""GET /telemetry/latest and GET /telemetry/history."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.telemetry import AssetLatestTelemetryOut, TelemetryHistoryOut
from app.services.telemetry_service import TelemetryService

router = APIRouter()


@router.get(
    "/telemetry/latest",
    response_model=list[AssetLatestTelemetryOut],
)
def latest_telemetry(
    utility: str | None = Query(default="water"),
    db: Session = Depends(db_dep),
) -> list[AssetLatestTelemetryOut]:
    return TelemetryService(db).latest_grouped_by_asset(utility=utility)


@router.get(
    "/telemetry/history",
    response_model=TelemetryHistoryOut,
)
def telemetry_history(
    asset_id: str | None = Query(default=None),
    metric_key: str | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=50_000),
    db: Session = Depends(db_dep),
) -> TelemetryHistoryOut:
    """Chronological telemetry from PostgreSQL for ML dataset builders."""
    return TelemetryService(db).history(
        asset_id=asset_id,
        metric_key=metric_key,
        start=from_,
        end=to,
        limit=limit,
    )
