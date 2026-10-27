"""GET /resilience — corridor & zone resilience posture + diagnostic (Phase 14.14)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.resilience import ResilienceContextOut
from app.api.schemas.resilience_diagnostic import ResilienceDiagnosticOut
from app.services.resilience_assessment_service import ResilienceAssessmentService
from app.services.resilience_diagnostic_service import ResilienceDiagnosticService

router = APIRouter()


@router.get("/resilience", response_model=ResilienceContextOut)
def get_resilience_context(
    hours: int = Query(
        default=24,
        ge=1,
        le=168,
        description="Forecast horizon forwarded to weather/environment impact assessments",
    ),
    utility_kind: str | None = Query(
        default=None,
        description="Optional utility context; defaults to first ENABLED_UTILITIES kind",
    ),
    db: Session = Depends(db_dep),
) -> ResilienceContextOut:
    """
    Corridor and zone resilience posture composed from existing impact assessments.

    POC operational posture — not a 0–100 score. Does not fabricate measurements.
    """
    return ResilienceAssessmentService(db).get_context(
        utility_kind=utility_kind,
        hours=hours,
    )


@router.get("/resilience/diagnostic", response_model=ResilienceDiagnosticOut)
def get_resilience_diagnostic(
    hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(db_dep),
) -> ResilienceDiagnosticOut:
    """
    Corridor operational diagnostic for the Resilience command page.

    Composes Water/Power dashboards, weather/environment context, alerts, and
    work orders. Categorical postures only (HEALTHY / STABLE / AT RISK / CRITICAL).
    """
    return ResilienceDiagnosticService(db).get_diagnostic(hours=hours)
