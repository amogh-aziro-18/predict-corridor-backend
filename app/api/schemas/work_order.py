"""Work order API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import WorkOrderSource, WorkOrderStatus


class WorkOrderCreate(BaseModel):
    asset_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    priority: str = Field(default="medium", description="critical | high | medium | low")
    source: str = Field(default=WorkOrderSource.MANUAL.value)
    recommendation_id: str | None = None
    recommendation_snapshot: dict[str, Any] | None = None
    assigned_technician_id: str | None = None
    due_at: datetime | None = None
    created_by: str | None = None


class WorkOrderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    priority: str | None = None
    due_at: datetime | None = None
    assigned_technician_id: str | None = None


class WorkOrderStatusUpdate(BaseModel):
    status: str = Field(description="Target lifecycle status")
    actor: str | None = None
    notes: str | None = None
    assigned_technician_id: str | None = None
    completion_notes: str | None = None
    verification_notes: str | None = None


class WorkOrderEventOut(BaseModel):
    id: str
    work_order_id: str
    from_status: str | None
    to_status: str
    actor: str | None
    notes: str | None
    created_at: datetime


class WorkOrderOut(BaseModel):
    id: str
    asset_id: str
    utility: str
    zone_id: str
    recommendation_id: str | None
    recommendation_snapshot: dict[str, Any] | None
    title: str
    description: str | None
    priority: str
    status: str
    assigned_technician_id: str | None
    source: str
    created_by: str | None
    created_at: datetime
    due_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    verified_at: datetime | None
    closed_at: datetime | None
    completion_notes: str | None
    verification_notes: str | None
    events: list[WorkOrderEventOut] = Field(default_factory=list)


ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    WorkOrderStatus.OPEN.value: {
        WorkOrderStatus.ASSIGNED.value,
        WorkOrderStatus.IN_PROGRESS.value,
    },
    WorkOrderStatus.ASSIGNED.value: {WorkOrderStatus.IN_PROGRESS.value},
    WorkOrderStatus.IN_PROGRESS.value: {WorkOrderStatus.COMPLETED.value},
    WorkOrderStatus.COMPLETED.value: {WorkOrderStatus.CLOSED.value},
    WorkOrderStatus.CLOSED.value: set(),
}
