"""GET /ai/recommendations — operational intelligence recommendations."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.recommendation import RecommendationOut
from app.services.recommendation_service import RecommendationService

router = APIRouter()


@router.get("/ai/recommendations", response_model=list[RecommendationOut])
def list_ai_recommendations(db: Session = Depends(db_dep)) -> list[RecommendationOut]:
    """Legacy AI route — same deterministic recommendations as /recommendations."""
    return RecommendationService(db).list_recommendations()
