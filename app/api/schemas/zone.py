"""Zone API schemas (aligned with frontend Zone contract)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ZoneOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    code: str
    area_sq_km: float = Field(serialization_alias="areaSqKm")
    health: float
    population: int
    utilities: list[str]
    critical_assets: int = Field(serialization_alias="criticalAssets")
