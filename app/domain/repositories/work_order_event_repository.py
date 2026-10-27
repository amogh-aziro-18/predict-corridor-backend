"""Work order audit event repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import WorkOrderEvent


class WorkOrderEventRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, event_id: str) -> WorkOrderEvent | None:
        return self._db.get(WorkOrderEvent, event_id)

    def list_for_work_order(self, work_order_id: str) -> list[WorkOrderEvent]:
        stmt = (
            select(WorkOrderEvent)
            .where(WorkOrderEvent.work_order_id == work_order_id)
            .order_by(WorkOrderEvent.created_at.asc())
        )
        return list(self._db.scalars(stmt).all())

    def add(self, event: WorkOrderEvent) -> WorkOrderEvent:
        self._db.add(event)
        self._db.flush()
        return event

    def save(self, event: WorkOrderEvent) -> WorkOrderEvent:
        self._db.add(event)
        self._db.commit()
        self._db.refresh(event)
        return event
