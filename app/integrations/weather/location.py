"""Resolve weather lookup coordinates from configured operational location."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.repositories.asset_repository import AssetRepository
from app.integrations.corridor_location import get_operational_location
from app.integrations.weather.base import WeatherLocation


def resolve_weather_location(
    db: Session,
    *,
    zone_id: str | None = None,
    utility_kind: str = "water",
) -> WeatherLocation:
    """
    Resolve the weather provider lookup point.

    Phase 14.12: uses the configured Delhi, India operational location
    (not asset-centroid invention, not Bangalore). ``utility_kind`` and
    ``asset_count`` remain for context / registry awareness.
    """
    kind = (utility_kind or "water").strip().lower() or "water"
    op = get_operational_location(zone_id=zone_id)
    assets = AssetRepository(db).list(utility_kind=kind, zone_id=zone_id)
    asset_count = sum(
        1
        for asset in assets
        if asset.latitude is not None and asset.longitude is not None
    )

    return WeatherLocation(
        latitude=op.latitude,
        longitude=op.longitude,
        scope=op.scope,
        zone_id=op.zone_id,
        asset_count=asset_count,
        utility_kind=kind,
        method=op.method,
        city=op.city,
        country=op.country,
        display_name=op.display_name,
        location_note=op.note,
    )
