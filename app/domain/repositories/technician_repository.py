"""Technician persistence repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Technician
from app.domain.query import normalize_utility_kind


class TechnicianRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, technician_id: str) -> Technician | None:
        return self._db.get(Technician, technician_id)

    def list(
        self,
        *,
        primary_zone_id: str | None = None,
        availability: str | None = None,
        utility: str | None = None,
    ) -> list[Technician]:
        stmt = select(Technician).order_by(Technician.name)
        if primary_zone_id:
            stmt = stmt.where(Technician.primary_zone_id == primary_zone_id)
        if availability:
            stmt = stmt.where(Technician.availability == availability)
        rows = list(self._db.scalars(stmt).all())
        kind = normalize_utility_kind(utility)
        if kind:
            rows = [
                row
                for row in rows
                if kind in [str(k).lower() for k in (row.utility_kinds or [])]
                or (
                    not (row.utility_kinds or [])
                    and kind == "water"
                )
            ]
        return rows

    def add(self, technician: Technician) -> Technician:
        self._db.add(technician)
        self._db.flush()
        return technician

    def save(self, technician: Technician) -> Technician:
        self._db.add(technician)
        self._db.commit()
        self._db.refresh(technician)
        return technician

    def delete(self, technician: Technician) -> None:
        self._db.delete(technician)
        self._db.commit()


def technician_qualified_for_utility(technician: Technician, utility: str | None) -> bool:
    """True when technician.utility_kinds includes the work-order utility."""
    kind = normalize_utility_kind(utility)
    if not kind:
        return True
    kinds = [str(k).lower() for k in (technician.utility_kinds or [])]
    if not kinds:
        kinds = ["water"]
    return kind in kinds
