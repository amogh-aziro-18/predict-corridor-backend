"""Read-only asset dependency inspection API (Phase 16.0)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.dependency import AssetDependenciesOut, DependencyEdgeOut
from app.services.asset_dependency_service import AssetDependencyService

router = APIRouter()


@router.get("/dependencies", response_model=list[DependencyEdgeOut])
def list_dependencies(
    source_asset_id: str | None = Query(default=None),
    target_asset_id: str | None = Query(default=None),
    dependency_type: str | None = Query(default=None),
    utility_kind: str | None = Query(default=None),
    zone_id: str | None = Query(default=None, alias="zone"),
    enabled: bool | None = Query(default=True),
    db: Session = Depends(db_dep),
) -> list[DependencyEdgeOut]:
    rows = AssetDependencyService(db).list_dependencies(
        source_asset_id=source_asset_id,
        target_asset_id=target_asset_id,
        dependency_type=dependency_type,
        utility_kind=utility_kind,
        zone_id=zone_id,
        enabled=enabled,
    )
    return [DependencyEdgeOut(**row) for row in rows]


@router.get("/dependencies/{asset_id}", response_model=AssetDependenciesOut)
def get_asset_dependencies(
    asset_id: str, db: Session = Depends(db_dep)
) -> AssetDependenciesOut:
    body = AssetDependencyService(db).get_dependencies_for_asset(asset_id)
    return AssetDependenciesOut(**body)
