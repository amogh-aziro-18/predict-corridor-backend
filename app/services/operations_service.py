"""Operations service — delegates work-order listing to WorkOrderService."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.work_order import WorkOrderOut
from app.services.work_order_service import WorkOrderService


class OperationsService:
    def __init__(self, db: Session) -> None:
        self._work_orders = WorkOrderService(db)

    def list_work_orders(self, **filters: object) -> list[WorkOrderOut]:
        return self._work_orders.list_work_orders(**filters)  # type: ignore[arg-type]
