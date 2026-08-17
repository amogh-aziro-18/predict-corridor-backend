"""Operational recommendations API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.recommendation import RecommendationOut
from app.services.recommendation_service import RecommendationService

router = APIRouter()


@router.get("/recommendations", response_model=list[RecommendationOut])
def list_recommendations(db: Session = Depends(db_dep)) -> list[RecommendationOut]:
    return RecommendationService(db).list_recommendations()


@router.get("/recommendations/high-priority", response_model=list[RecommendationOut])
def list_high_priority_recommendations(
    db: Session = Depends(db_dep),
) -> list[RecommendationOut]:
    return RecommendationService(db).list_high_priority()
