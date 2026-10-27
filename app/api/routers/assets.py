"""GET /assets — real database-backed asset API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.asset import AssetOut
from app.services.asset_service import AssetService

router = APIRouter()


@router.get("/assets", response_model=list[AssetOut], response_model_by_alias=True)
def list_assets(
    zone: str | None = Query(default=None, description="Zone filter: A, zone-a, Zone A"),
    utility: str | None = Query(default=None, description="Utility kind filter, e.g. water"),
    asset_type: str | None = Query(
        default=None, description="Asset type filter, e.g. pump_station"
    ),
    db: Session = Depends(db_dep),
) -> list[AssetOut]:
    return AssetService(db).list_assets(zone=zone, utility=utility, asset_type=asset_type)


@router.get("/assets/{asset_id}", response_model=AssetOut, response_model_by_alias=True)
def get_asset(asset_id: str, db: Session = Depends(db_dep)) -> AssetOut:
    return AssetService(db).get_asset(asset_id)
