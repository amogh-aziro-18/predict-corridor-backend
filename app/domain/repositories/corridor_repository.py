"""Corridor repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Corridor


class CorridorRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, corridor_id: str) -> Corridor | None:
        return self._db.get(Corridor, corridor_id)

    def list(self) -> list[Corridor]:
        return list(self._db.scalars(select(Corridor).order_by(Corridor.id)).all())

    def upsert(self, corridor: Corridor) -> Corridor:
        merged = self._db.merge(corridor)
        self._db.flush()
        return merged
