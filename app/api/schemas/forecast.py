"""Demand forecast API schemas (Sprint 6B)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ForecastPointOut(BaseModel):
    timestamp: datetime
    hours_ahead: int
    demand: float
    lower: float
    upper: float


class ActualDemandPointOut(BaseModel):
    """Observed demand from real telemetry (not fabricated)."""

    timestamp: datetime
    demand: float


class ForecastHorizonsOut(BaseModel):
    next_24_hours: list[ForecastPointOut] = Field(default_factory=list)
    next_48_hours: list[ForecastPointOut] = Field(default_factory=list)


class ForecastConfidenceOut(BaseModel):
    interval_width: float = 0.80
    mean_interval_pct: float | None = None
    level: str = "unknown"
    note: str | None = None


class DemandForecastOut(BaseModel):
    current_demand: float | None = None
    current_demand_at: datetime | None = None
    unit: str = "L/s"
    forecast: ForecastHorizonsOut
    confidence: ForecastConfidenceOut
    generated_at: datetime
    model_version: str
    training_window: dict[str, Any] = Field(default_factory=dict)
    demand_source: dict[str, Any] = Field(default_factory=dict)
    zone_id: str | None = None
    # Recent hourly actuals from PostgreSQL demand series (for charting).
    recent_actual: list[ActualDemandPointOut] = Field(default_factory=list)


class ForecastTrainOut(BaseModel):
    status: str
    model_version: str
    artifact_path: str
    training_window: dict[str, Any] = Field(default_factory=dict)
    demand_source: dict[str, Any] = Field(default_factory=dict)
    rows: int | None = None
    trained_at: str | None = None


class ForecastModelStatusOut(BaseModel):
    """Prophet demand model readiness (separate from XGBoost /ml/status)."""

    model: str
    model_version: str | None = None
    artifact_exists: bool
    artifact_path: str
    inference: str
    last_training_time: datetime | None = None
    training_window: dict[str, Any] = Field(default_factory=dict)
    demand_source: dict[str, Any] = Field(default_factory=dict)
    zone_id: str | None = None
