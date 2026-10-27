"""Work order persistence repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import WorkOrderStatus
from app.domain.models import WorkOrder

_ACTIVE_RECOMMENDATION_STATUSES = (
    WorkOrderStatus.OPEN.value,
    WorkOrderStatus.ASSIGNED.value,
    WorkOrderStatus.IN_PROGRESS.value,
    WorkOrderStatus.COMPLETED.value,
)


class WorkOrderRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, work_order_id: str) -> WorkOrder | None:
        return self._db.get(WorkOrder, work_order_id)

    def list(
        self,
        *,
        asset_id: str | None = None,
        utility: str | None = None,
        zone_id: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        assigned_technician_id: str | None = None,
    ) -> list[WorkOrder]:
        stmt = select(WorkOrder).order_by(WorkOrder.created_at.desc())
        if asset_id:
            stmt = stmt.where(WorkOrder.asset_id == asset_id)
        if utility:
            stmt = stmt.where(WorkOrder.utility == utility)
        if zone_id:
            stmt = stmt.where(WorkOrder.zone_id == zone_id)
        if status:
            stmt = stmt.where(WorkOrder.status == status)
        if priority:
            stmt = stmt.where(WorkOrder.priority == priority)
        if assigned_technician_id:
            stmt = stmt.where(WorkOrder.assigned_technician_id == assigned_technician_id)
        return list(self._db.scalars(stmt).all())

    def find_active_by_recommendation_id(self, recommendation_id: str) -> WorkOrder | None:
        stmt = (
            select(WorkOrder)
            .where(WorkOrder.recommendation_id == recommendation_id)
            .where(WorkOrder.status.in_(_ACTIVE_RECOMMENDATION_STATUSES))
            .order_by(WorkOrder.created_at.desc())
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def add(self, work_order: WorkOrder) -> WorkOrder:
        self._db.add(work_order)
        self._db.flush()
        return work_order

    def save(self, work_order: WorkOrder) -> WorkOrder:
        self._db.add(work_order)
        self._db.commit()
        self._db.refresh(work_order)
        return work_order

    def delete(self, work_order: WorkOrder) -> None:
        self._db.delete(work_order)
        self._db.commit()
