"""Power transformer predictive-maintenance API schemas (Phase 14.3C1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContributingFeatureOut(BaseModel):
    feature: str
    contribution: float
    direction: str


class PowerMaintenancePredictionOut(BaseModel):
    asset_id: str
    asset_name: str
    utility_kind: str = "power"
    affected_zone: str | None = None
    model_name: str
    model_version: str
    assessment_kind: str = Field(
        description="predictive_maintenance_model — distinct from rule-based assessment"
    )
    prediction: str
    maintenance_risk: float = Field(ge=0.0, le=1.0)
    risk_probability: float = Field(ge=0.0, le=1.0)
    risk_level: str
    model_certainty: float = Field(
        ge=0.0,
        le=1.0,
        description="Distance of model probability from 0.5 — not fabricated accuracy",
    )
    top_contributing_features: list[ContributingFeatureOut] = Field(default_factory=list)
    feature_evidence: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime
    model_available: bool = True


class PowerMaintenanceTrainOut(BaseModel):
    status: str
    model: str
    model_version: str
    utility_kind: str = "power"
    assessment_kind: str = "predictive_maintenance_model"
    artifact_path: str
    trained_at: datetime
    training_window: dict[str, Any] = Field(default_factory=dict)
    rows: int
    assets: int
    label_distribution: dict[str, int] = Field(default_factory=dict)
    label_strategy: str | None = None
    validation_metrics: dict[str, Any] = Field(default_factory=dict)
    feature_names: list[str] = Field(default_factory=list)


class PowerMaintenanceModelStatusOut(BaseModel):
    model: str
    model_version: str | None = None
    utility_kind: str = "power"
    assessment_kind: str = "predictive_maintenance_model"
    artifact_exists: bool
    artifact_path: str
    last_training_time: datetime | None = None
    training_window: dict[str, Any] = Field(default_factory=dict)
    training_metadata: dict[str, Any] = Field(default_factory=dict)
    inference: str
    expected_model_version: str | None = None
