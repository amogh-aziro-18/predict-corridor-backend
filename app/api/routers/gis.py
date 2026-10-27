"""GIS operational context + GeoJSON layers API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.common import MessageResponse
from app.api.schemas.gis import GisWeatherContextOut
from app.api.schemas.gis_operational import GisOperationalLayersOut
from app.services.gis_operational_service import GisOperationalService
from app.services.gis_service import GisService

router = APIRouter()


@router.get("/gis", response_model=MessageResponse)
def get_gis_summary() -> MessageResponse:
    """Discovery endpoint for GIS capabilities."""
    return MessageResponse(
        message=(
            "GIS layers: GET /gis/layers (GeoJSON assets + zone anchors). "
            "Context: GET /gis/context (weather/environment/resilience). "
            "Asset registry: GET /assets. Zones: GET /zones."
        )
    )


@router.get("/gis/layers", response_model=GisOperationalLayersOut)
def get_gis_operational_layers(
    utility: str | None = Query(default=None, description="Filter assets by utility_kind"),
    zone: str | None = Query(default=None, description="Filter assets by zone_id"),
    db: Session = Depends(db_dep),
) -> GisOperationalLayersOut:
    """
    Operational GeoJSON for the GIS Command Center.

    Asset points use real PostgreSQL lat/lon only. Zone points are Delhi
    operational anchors (not surveyed polygons). Health/status/risk reuse
    the asset registry; alerts and work orders are joined from existing tables.
    """
    return GisOperationalService(db).get_layers(utility=utility, zone_id=zone)


@router.get("/gis/context", response_model=GisWeatherContextOut)
def get_gis_weather_context(
    hours: int = Query(default=48, ge=1, le=168, description="Forecast horizon for impact rules"),
    db: Session = Depends(db_dep),
) -> GisWeatherContextOut:
    """
    Corridor and zone weather, environmental impact, and resilience posture for GIS.

    Weather, environment, and resilience are corridor/zone scoped; assets are not
    enriched with per-asset weather, pollution, or resilience values.
    """
    return GisService(db).get_weather_context(forecast_hours=hours)
