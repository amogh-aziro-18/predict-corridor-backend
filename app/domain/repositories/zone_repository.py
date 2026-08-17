"""Zone repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Zone


class ZoneRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, zone_id: str) -> Zone | None:
        return self._db.get(Zone, zone_id)

    def list(self, corridor_id: str | None = None) -> list[Zone]:
        stmt = select(Zone).order_by(Zone.id)
        if corridor_id:
            stmt = stmt.where(Zone.corridor_id == corridor_id)
        return list(self._db.scalars(stmt).all())

    def list_by_corridor(self, corridor_id: str) -> list[Zone]:
        return self.list(corridor_id=corridor_id)

    def upsert(self, zone: Zone) -> Zone:
        merged = self._db.merge(zone)
        self._db.flush()
        return merged
