"""GIS operational GeoJSON schemas (Phase 14.13)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GisGeoJsonFeatureCollectionOut(BaseModel):
    """RFC 7946 FeatureCollection for operational map layers."""

    type: str = "FeatureCollection"
    features: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class GisOperationalLayersOut(BaseModel):
    """
    Backend-driven GIS layers for the command center.

    Asset points use real registry lat/lon only.
    Zone features are operational anchors (not surveyed polygons).
    """

    assets: GisGeoJsonFeatureCollectionOut
    zones: GisGeoJsonFeatureCollectionOut
    corridor: dict[str, Any] = Field(
        description="Configured Delhi corridor context (map center hint only)."
    )
    notes: list[str] = Field(default_factory=list)
