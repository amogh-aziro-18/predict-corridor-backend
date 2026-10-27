"""Incident intelligence API schemas (Phase 11B) — compose-only context."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.api.schemas.asset import AssetOut
from app.api.schemas.environment_impact import EnvironmentImpactAssessmentOut
from app.api.schemas.incident import IncidentOut
from app.api.schemas.resilience import ResilienceAssessmentOut
from app.api.schemas.technician import TechnicianOut
from app.api.schemas.weather_impact import WeatherImpactAssessmentOut
from app.api.schemas.work_order import WorkOrderOut


class IncidentAssessmentScopeOut(BaseModel):
    incident_scope: str
    weather_env_resilience_zone_id: str | None = None
    utility_kind: str
    note: str


class IncidentRecommendedActionOut(BaseModel):
    """Read-only placeholder in 11B — no incident recommendation engine."""

    summary: str | None = None
    source: str | None = None
    recommendation_id: str | None = None
    confidence: float | None = None


class IncidentInputsAvailabilityOut(BaseModel):
    weather: bool = False
    environment: bool = False
    resilience: bool = False


class IncidentIntelligenceOut(BaseModel):
    """Canonical situational picture for one Incident (compose-only)."""

    incident: IncidentOut
    assessment_scope: IncidentAssessmentScopeOut
    weather: WeatherImpactAssessmentOut | None = None
    environment: EnvironmentImpactAssessmentOut | None = None
    resilience: ResilienceAssessmentOut | None = None
    assets: list[AssetOut] = Field(default_factory=list)
    work_orders: list[WorkOrderOut] = Field(default_factory=list)
    technicians: list[TechnicianOut] = Field(default_factory=list)
    recommended_action: IncidentRecommendedActionOut = Field(
        default_factory=IncidentRecommendedActionOut
    )
    inputs_available: IncidentInputsAvailabilityOut
    assessed_at: datetime
