"""GIS operational context schemas (8E weather + 9E environment + 10B resilience)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.api.schemas.environment_impact import EnvironmentImpactAssessmentOut
from app.api.schemas.resilience import ResilienceAssessmentOut
from app.api.schemas.weather_impact import WeatherImpactAssessmentOut


class GisZoneWeatherContextOut(BaseModel):
    """Zone-level weather impact context at the zone asset centroid."""

    zone_id: str
    zone_name: str
    assessment: WeatherImpactAssessmentOut | None = None
    unavailable: bool = Field(
        default=False,
        description="True when weather impact could not be assessed for this zone.",
    )


class GisZoneEnvironmentContextOut(BaseModel):
    """Zone-level environmental impact context at the zone asset centroid."""

    zone_id: str
    zone_name: str
    assessment: EnvironmentImpactAssessmentOut | None = None
    unavailable: bool = Field(
        default=False,
        description="True when environmental impact could not be assessed for this zone.",
    )


class GisZoneResilienceContextOut(BaseModel):
    """Zone-level resilience posture context (corridor/zone scope only)."""

    zone_id: str
    zone_name: str
    assessment: ResilienceAssessmentOut | None = None
    unavailable: bool = Field(
        default=False,
        description="True when resilience posture could not be assessed for this zone.",
    )


class GisEnvironmentContextOut(BaseModel):
    """
    Corridor + zone environmental impact for GIS.

    Air quality / environment is centroid-based — not measured per asset.
    Static asset weather_exposure / flood_risk remain separate seed metadata.
    """

    spatial_note: str = Field(
        description="Explains centroid-based environment scope; no per-asset pollution.",
    )
    corridor: EnvironmentImpactAssessmentOut | None = None
    corridor_unavailable: bool = Field(
        default=False,
        description="True when corridor environmental impact could not be assessed.",
    )
    zones: list[GisZoneEnvironmentContextOut] = Field(default_factory=list)


class GisResilienceContextOut(BaseModel):
    """
    Corridor + zone resilience posture for GIS (Phase 10B).

    Resilience is composed at corridor/zone scope only — not measured per asset.
    Static weather_exposure / flood_risk remain seed metadata and are not live flood risk.
    """

    spatial_note: str = Field(
        description=(
            "Resilience is assessed at corridor/zone scope; "
            "no asset-level resilience measurement is available."
        ),
    )
    corridor: ResilienceAssessmentOut | None = None
    corridor_unavailable: bool = Field(
        default=False,
        description="True when corridor resilience posture could not be assessed.",
    )
    zones: list[GisZoneResilienceContextOut] = Field(default_factory=list)


class GisWeatherContextOut(BaseModel):
    """
    GIS operational context for corridor and registered zones.

    Weather, environment, and resilience are evaluated at corridor/zone scope —
    not at individual asset coordinates. Assets remain in the separate ``GET /assets``
    registry.
    """

    spatial_note: str = Field(
        description="Explains centroid-based weather scope; no per-asset weather.",
    )
    utility_kind: str
    corridor: WeatherImpactAssessmentOut
    zones: list[GisZoneWeatherContextOut] = Field(default_factory=list)
    environment: GisEnvironmentContextOut | None = Field(
        default=None,
        description="Live environmental impact context (Phase 9E).",
    )
    resilience: GisResilienceContextOut | None = Field(
        default=None,
        description="Live resilience posture context (Phase 10B).",
    )
