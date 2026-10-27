"""GET /operations/work-orders — compatibility listing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.work_order import WorkOrderOut
from app.services.work_order_service import WorkOrderService

router = APIRouter()


@router.get("/operations/work-orders", response_model=list[WorkOrderOut])
def list_work_orders(
    status: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    asset_id: str | None = Query(default=None),
    zone_id: str | None = Query(default=None, alias="zone"),
    utility: str | None = Query(default=None),
    assigned_technician_id: str | None = Query(default=None),
    db: Session = Depends(db_dep),
) -> list[WorkOrderOut]:
    """Backward-compatible operations queue endpoint."""
    return WorkOrderService(db).list_work_orders(
        status=status,
        priority=priority,
        asset_id=asset_id,
        zone_id=zone_id,
        utility=utility,
        assigned_technician_id=assigned_technician_id,
    )
