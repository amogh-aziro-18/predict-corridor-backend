"""Utility repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Utility


class UtilityRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, utility_id: str) -> Utility | None:
        return self._db.get(Utility, utility_id)

    def get_by_kind(self, kind: str, corridor_id: str | None = None) -> Utility | None:
        stmt = select(Utility).where(Utility.kind == kind)
        if corridor_id:
            stmt = stmt.where(Utility.corridor_id == corridor_id)
        return self._db.scalars(stmt.limit(1)).first()

    def list_enabled(self, corridor_id: str | None = None) -> list[Utility]:
        stmt = select(Utility).where(Utility.enabled.is_(True)).order_by(Utility.kind)
        if corridor_id:
            stmt = stmt.where(Utility.corridor_id == corridor_id)
        return list(self._db.scalars(stmt).all())

    def upsert(self, utility: Utility) -> Utility:
        merged = self._db.merge(utility)
        self._db.flush()
        return merged
