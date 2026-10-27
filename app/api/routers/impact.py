"""Read-only cross-utility impact assessment API (Phase 16.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.impact import AssetImpactAssessmentOut, AssetImpactRequest
from app.services.cross_utility_impact_service import CrossUtilityImpactService

router = APIRouter()


@router.post("/impact/asset", response_model=AssetImpactAssessmentOut)
def assess_asset_impact(
    body: AssetImpactRequest,
    db: Session = Depends(db_dep),
) -> AssetImpactAssessmentOut:
    """
    Deterministic dependency impact for an explicit asset condition.

    Does not mutate telemetry, health, alerts, or work orders.
    """
    result = CrossUtilityImpactService(db).assess_asset_impact(
        asset_id=body.asset_id,
        condition=body.condition,
        max_depth=body.max_depth,
    )
    return AssetImpactAssessmentOut(**result)
