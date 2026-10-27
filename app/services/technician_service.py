"""Technician registry service — read-only for POC."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.technician import TechnicianOut
from app.core.errors import NotFoundError
from app.domain.repositories import TechnicianRepository


def _to_out(technician) -> TechnicianOut:
    kinds = [str(k).lower() for k in (technician.utility_kinds or [])]
    if not kinds:
        kinds = ["water"]
    return TechnicianOut(
        id=technician.id,
        name=technician.name,
        role=technician.role,
        primary_zone_id=technician.primary_zone_id,
        skills=list(technician.skills or []),
        utility_kinds=kinds,
        availability=technician.availability,
        emergency_certified=technician.emergency_certified,
        created_at=technician.created_at,
        updated_at=technician.updated_at,
    )


class TechnicianService:
    def __init__(self, db: Session) -> None:
        self._technicians = TechnicianRepository(db)

    def list_technicians(
        self,
        *,
        primary_zone_id: str | None = None,
        availability: str | None = None,
        utility: str | None = None,
    ) -> list[TechnicianOut]:
        rows = self._technicians.list(
            primary_zone_id=primary_zone_id,
            availability=availability,
            utility=utility,
        )
        return [_to_out(row) for row in rows]

    def get_technician(self, technician_id: str) -> TechnicianOut:
        technician = self._technicians.get(technician_id)
        if technician is None:
            raise NotFoundError(f"Technician not found: {technician_id}")
        return _to_out(technician)
