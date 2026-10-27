"""Resolve environment lookup coordinates from configured operational location."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.domain.repositories.asset_repository import AssetRepository
from app.integrations.corridor_location import get_operational_location
from app.integrations.environment.base import EnvironmentLocation


def resolve_environment_location(
    db: Session,
    *,
    zone_id: str | None = None,
    utility_kind: str,
) -> EnvironmentLocation:
    """
    Resolve the environment provider lookup point.

    Phase 14.12: uses the configured Delhi, India operational location.
    ``utility_kind`` is required for registry context (not for inventing coords).
    """
    kind = utility_kind.strip().lower()
    if not kind:
        raise ValidationAppError("utility_kind is required for environment location resolution")

    op = get_operational_location(zone_id=zone_id)
    assets = AssetRepository(db).list(utility_kind=kind, zone_id=zone_id)
    asset_count = sum(
        1
        for asset in assets
        if asset.latitude is not None and asset.longitude is not None
    )

    return EnvironmentLocation(
        latitude=op.latitude,
        longitude=op.longitude,
        scope=op.scope,
        zone_id=op.zone_id,
        asset_count=asset_count,
        utility_kind=kind,
        method=op.method,
        timezone=op.timezone_hint,
        city=op.city,
        country=op.country,
        display_name=op.display_name,
        location_note=op.note,
    )
