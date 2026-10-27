"""Work order CRUD and lifecycle API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.work_order import (
    WorkOrderCreate,
    WorkOrderOut,
    WorkOrderStatusUpdate,
    WorkOrderUpdate,
)
from app.services.work_order_service import WorkOrderService

router = APIRouter()


@router.post("/work-orders", response_model=WorkOrderOut, status_code=201)
def create_work_order(
    body: WorkOrderCreate,
    db: Session = Depends(db_dep),
) -> WorkOrderOut:
    return WorkOrderService(db).create_work_order(body)


@router.get("/work-orders", response_model=list[WorkOrderOut])
def list_work_orders(
    status: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    asset_id: str | None = Query(default=None),
    zone_id: str | None = Query(default=None, alias="zone"),
    utility: str | None = Query(default=None),
    assigned_technician_id: str | None = Query(default=None),
    db: Session = Depends(db_dep),
) -> list[WorkOrderOut]:
    return WorkOrderService(db).list_work_orders(
        status=status,
        priority=priority,
        asset_id=asset_id,
        zone_id=zone_id,
        utility=utility,
        assigned_technician_id=assigned_technician_id,
    )


@router.get("/work-orders/{work_order_id}", response_model=WorkOrderOut)
def get_work_order(work_order_id: str, db: Session = Depends(db_dep)) -> WorkOrderOut:
    return WorkOrderService(db).get_work_order(work_order_id)


@router.patch("/work-orders/{work_order_id}", response_model=WorkOrderOut)
def update_work_order(
    work_order_id: str,
    body: WorkOrderUpdate,
    db: Session = Depends(db_dep),
) -> WorkOrderOut:
    return WorkOrderService(db).update_work_order(work_order_id, body)


@router.patch("/work-orders/{work_order_id}/status", response_model=WorkOrderOut)
def update_work_order_status(
    work_order_id: str,
    body: WorkOrderStatusUpdate,
    db: Session = Depends(db_dep),
) -> WorkOrderOut:
    return WorkOrderService(db).update_work_order_status(work_order_id, body)
