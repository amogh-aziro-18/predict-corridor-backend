"""Predictive-maintenance API schemas (Sprint 6C)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContributingFeatureOut(BaseModel):
    feature: str
    contribution: float
    direction: str


class MaintenancePredictionOut(BaseModel):
    asset_id: str
    asset_name: str
    affected_zone: str | None = None
    maintenance_risk: float = Field(ge=0.0, le=1.0)
    risk_level: str
    confidence: float = Field(ge=0.0, le=1.0)
    predicted_failure_days: int = Field(ge=1)
    # Truthful: days are a monotonic map from risk, not a dedicated RUL model.
    horizon_basis: str = "probability_mapped"
    top_contributing_features: list[ContributingFeatureOut] = Field(default_factory=list)
    generated_at: datetime
    model_version: str


class MaintenanceTrainOut(BaseModel):
    status: str
    model_version: str
    artifact_path: str
    trained_at: datetime
    training_window: dict[str, Any] = Field(default_factory=dict)
    rows: int
    assets: int
    label_distribution: dict[str, int] = Field(default_factory=dict)
    label_strategy: str | None = None


class RelatedModelStatusOut(BaseModel):
    model: str
    model_version: str | None = None
    artifact_exists: bool
    artifact_name: str
    inference: str


class MaintenanceModelStatusOut(BaseModel):
    model: str
    model_version: str | None = None
    artifact_exists: bool
    artifact_path: str
    last_training_time: datetime | None = None
    training_window: dict[str, Any] = Field(default_factory=dict)
    training_metadata: dict[str, Any] = Field(default_factory=dict)
    # Extensible multi-model snapshot (Prophet + XGBoost, etc.)
    models: list[RelatedModelStatusOut] = Field(default_factory=list)
