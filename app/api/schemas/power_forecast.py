"""Power load forecast API schemas (Phase 14.3C2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PowerForecastPointOut(BaseModel):
    timestamp: datetime
    hours_ahead: float
    value: float
    lower: float
    upper: float


class PowerActualLoadPointOut(BaseModel):
    timestamp: datetime
    value: float


class PowerForecastConfidenceOut(BaseModel):
    interval_width: float = 0.80
    level: str = "poc"
    note: str | None = None


class PowerLoadForecastOut(BaseModel):
    utility_kind: str = "power"
    model_name: str
    model_version: str
    assessment_kind: str = "load_forecast_model"
    target_metric: str = "active_power_mw"
    unit: str = "MW"
    current_load: float | None = None
    current_load_at: datetime | None = None
    forecast_horizon: str
    forecast_hours: int | None = None
    limited_history: bool = False
    model_available: bool = True
    generated_at: datetime
    training_window: dict[str, Any] = Field(default_factory=dict)
    demand_source: dict[str, Any] = Field(default_factory=dict)
    zone_id: str | None = None
    forecast_points: list[PowerForecastPointOut] = Field(default_factory=list)
    recent_actual: list[PowerActualLoadPointOut] = Field(default_factory=list)
    confidence: PowerForecastConfidenceOut


class PowerForecastTrainOut(BaseModel):
    status: str
    model: str
    model_version: str
    utility_kind: str = "power"
    target_metric: str = "active_power_mw"
    assessment_kind: str = "load_forecast_model"
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


class PowerForecastModelStatusOut(BaseModel):
    model: str
    model_version: str | None = None
    utility_kind: str = "power"
    assessment_kind: str = "load_forecast_model"
    target_metric: str = "active_power_mw"
    artifact_exists: bool
    artifact_path: str
    inference: str
    last_training_time: datetime | None = None
    training_window: dict[str, Any] = Field(default_factory=dict)
    demand_source: dict[str, Any] = Field(default_factory=dict)
    expected_model_version: str | None = None
    zone_id: str | None = None
    limited_history: bool | None = None
