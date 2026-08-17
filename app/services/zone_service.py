"""Zone service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.zone import ZoneOut
from app.core.errors import NotFoundError
from app.domain.query import normalize_zone_id
from app.domain.repositories import ZoneRepository
from app.services.mappers import zone_to_out


class ZoneService:
    def __init__(self, db: Session) -> None:
        self._zones = ZoneRepository(db)

    def list_zones(self, corridor_id: str | None = None) -> list[ZoneOut]:
        return [zone_to_out(z) for z in self._zones.list(corridor_id=corridor_id)]

    def get_zone(self, zone_id: str) -> ZoneOut:
        normalized = normalize_zone_id(zone_id) or zone_id
        zone = self._zones.get(normalized)
        if zone is None:
            raise NotFoundError(f"Zone not found: {zone_id}")
        return zone_to_out(zone)
