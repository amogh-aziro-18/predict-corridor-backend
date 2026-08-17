"""Telemetry API / MQTT envelope schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TelemetryEnvelope(BaseModel):
    """Canonical MQTT telemetry payload."""

    value: float
    unit: str = ""
    quality: str = "good"
    recorded_at: datetime | None = None
    metric_key: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)


class TelemetryOut(BaseModel):
    id: str
    sensor_id: str
    metric_key: str
    value: float
    unit: str
    quality: str
    recorded_at: datetime


class MetricLatestOut(BaseModel):
    metric_key: str
    value: float
    unit: str
    quality: str
    recorded_at: datetime


class AssetLatestTelemetryOut(BaseModel):
    asset_id: str
    asset_name: str
    zone: str
    utility: str
    status: str
    health_score: float
    metrics: list[MetricLatestOut] = Field(default_factory=list)


class TelemetryHistoryPointOut(BaseModel):
    id: str
    sensor_id: str
    asset_id: str
    asset_name: str
    metric_key: str
    value: float
    unit: str
    quality: str
    recorded_at: datetime


class TelemetryHistoryOut(BaseModel):
    asset_id: str | None = None
    metric_key: str | None = None
    from_time: datetime | None = Field(default=None, alias="from")
    to_time: datetime | None = Field(default=None, alias="to")
    count: int
    points: list[TelemetryHistoryPointOut] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
