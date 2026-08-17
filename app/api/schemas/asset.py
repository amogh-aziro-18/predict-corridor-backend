"""Asset API schemas."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssetOut(BaseModel):
    """
    Public asset projection.

    Exposes both frontend field names (`id`, `name`, camelCase) and Sprint 2
    names (`asset_id`, `asset_name`, `health_score`).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    asset_id: str
    name: str
    asset_name: str
    zone: str
    utility: str
    type: str
    latitude: float | None = None
    longitude: float | None = None
    status: str
    health: float
    health_score: float
    risk: str
    weather_exposure: str = Field(serialization_alias="weatherExposure")
    flood_risk: str = Field(serialization_alias="floodRisk")
    criticality: str
    backup: str
    recovery_priority: int = Field(serialization_alias="recoveryPriority")
    last_maintenance: date | None = Field(default=None, serialization_alias="lastMaintenance")
    next_inspection: date | None = Field(default=None, serialization_alias="nextInspection")
    predicted_failure_days: int | None = Field(
        default=None, serialization_alias="predictedFailureDays"
    )
    confidence: float
    profile: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def mirror_ids(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "id" in data and "asset_id" not in data:
                data["asset_id"] = data["id"]
            if "name" in data and "asset_name" not in data:
                data["asset_name"] = data["name"]
            if "health" in data and "health_score" not in data:
                data["health_score"] = data["health"]
        return data
