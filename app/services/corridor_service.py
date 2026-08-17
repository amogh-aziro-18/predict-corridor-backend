"""Corridor service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.corridor import CorridorOut
from app.core.errors import NotFoundError
from app.domain.repositories import CorridorRepository, ZoneRepository
from app.services.mappers import corridor_to_out


class CorridorService:
    def __init__(self, db: Session) -> None:
        self._corridors = CorridorRepository(db)
        self._zones = ZoneRepository(db)

    def list_corridors(self) -> list[CorridorOut]:
        result: list[CorridorOut] = []
        for c in self._corridors.list():
            zones = self._zones.list_by_corridor(c.id)
            result.append(corridor_to_out(c, zones))
        return result

    def get_corridor(self, corridor_id: str) -> CorridorOut:
        corridor = self._corridors.get(corridor_id)
        if corridor is None:
            raise NotFoundError(f"Corridor not found: {corridor_id}")
        zones = self._zones.list_by_corridor(corridor.id)
        return corridor_to_out(corridor, zones)
