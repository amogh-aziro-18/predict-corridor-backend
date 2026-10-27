"""Gas demand forecast API schemas (Phase 15.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GasForecastPointOut(BaseModel):
    timestamp: datetime
    hours_ahead: float
    value: float
    lower: float
    upper: float
    kind: str = "forecast"


class GasActualDemandPointOut(BaseModel):
    timestamp: datetime
    value: float
    kind: str = "actual"


class GasForecastConfidenceOut(BaseModel):
    interval_width: float = 0.80
    level: str = "poc"
    note: str | None = None


class GasDemandForecastOut(BaseModel):
    utility_kind: str = "gas"
    model_name: str
    model_version: str
    provider: str = "prophet"
    assessment_kind: str = "demand_forecast_model"
    target_metric: str = "flow_rate_nm3_h"
    unit: str = "Nm³/h"
    status: str = "ok"
    source: str = "model_forecast"
    current_demand: float | None = None
    current_demand_at: datetime | None = None
    forecast_horizon: str
    forecast_hours: int | None = None
    limited_history: bool = False
    model_available: bool = True
    generated_at: datetime
    training_window: dict[str, Any] = Field(default_factory=dict)
    demand_source: dict[str, Any] = Field(default_factory=dict)
    zone_id: str | None = None
    forecast_points: list[GasForecastPointOut] = Field(default_factory=list)
    recent_actual: list[GasActualDemandPointOut] = Field(default_factory=list)
    confidence: GasForecastConfidenceOut


class GasForecastTrainOut(BaseModel):
    status: str
    model: str
    model_version: str
    utility_kind: str = "gas"
    target_metric: str = "flow_rate_nm3_h"
    assessment_kind: str = "demand_forecast_model"
    artifact_path: str
    training_window: dict[str, Any] = Field(default_factory=dict)
    demand_source: dict[str, Any] = Field(default_factory=dict)
    rows: int | None = None
    trained_at: datetime | None = None
    resample_freq: str | None = None
    validation_metrics: dict[str, Any] = Field(default_factory=dict)
    validation_note: str | None = None
    limited_history: bool = False
    zone_id: str | None = None


class GasForecastModelStatusOut(BaseModel):
    model: str
    model_version: str | None = None
    utility_kind: str = "gas"
    assessment_kind: str = "demand_forecast_model"
    target_metric: str = "flow_rate_nm3_h"
    provider: str = "prophet"
    artifact_exists: bool
    artifact_path: str
    inference: str
    status: str
    last_training_time: datetime | None = None
    training_window: dict[str, Any] = Field(default_factory=dict)
    demand_source: dict[str, Any] = Field(default_factory=dict)
    expected_model_version: str | None = None
    zone_id: str | None = None
    limited_history: bool | None = None
