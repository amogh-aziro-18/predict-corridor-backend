"""Corridor API schema."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.zone import ZoneOut


class CorridorOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    authority: str
    length_km: float = Field(serialization_alias="lengthKm")
    zones: list[ZoneOut] = Field(default_factory=list)
