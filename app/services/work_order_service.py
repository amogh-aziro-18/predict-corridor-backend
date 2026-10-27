"""Work order lifecycle service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas.work_order import (
    ALLOWED_STATUS_TRANSITIONS,
    WorkOrderCreate,
    WorkOrderEventOut,
    WorkOrderOut,
    WorkOrderStatusUpdate,
    WorkOrderUpdate,
)
from app.core.errors import NotFoundError, ValidationAppError
from app.domain.enums import Severity, WorkOrderSource, WorkOrderStatus
from app.domain.models import WorkOrder, WorkOrderEvent
from app.domain.query import normalize_utility_kind, normalize_zone_id
from app.domain.repositories import (
    AssetRepository,
    TechnicianRepository,
    WorkOrderEventRepository,
    WorkOrderRepository,
)
from app.domain.repositories.technician_repository import (
    technician_qualified_for_utility,
)


def _new_work_order_id() -> str:
    return f"WO-{uuid.uuid4().hex[:12]}"


def _new_event_id() -> str:
    return f"WOE-{uuid.uuid4().hex[:12]}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkOrderService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._technicians = TechnicianRepository(db)
        self._work_orders = WorkOrderRepository(db)
        self._events = WorkOrderEventRepository(db)

    def create_work_order(self, payload: WorkOrderCreate) -> WorkOrderOut:
        asset_id = (payload.asset_id or "").strip()
        if self._is_synthetic_scope_asset_id(asset_id):
            raise ValidationAppError(
                "asset_id must be a physical asset ID; corridor/zone scope identifiers "
                f"({asset_id}) cannot create work orders"
            )

        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {payload.asset_id}")

        self._validate_priority(payload.priority)
        self._validate_source(payload.source)
        if payload.assigned_technician_id:
            self._require_technician_for_utility(
                payload.assigned_technician_id, asset.utility_kind
            )

        if payload.recommendation_id:
            existing = self._work_orders.find_active_by_recommendation_id(
                payload.recommendation_id
            )
            if existing is not None:
                raise ValidationAppError(
                    f"Active work order already exists for recommendation "
                    f"{payload.recommendation_id}: {existing.id} (status: {existing.status})"
                )

        work_order = WorkOrder(
            id=_new_work_order_id(),
            asset_id=asset.id,
            utility=asset.utility_kind,
            zone_id=asset.zone_id,
            recommendation_id=payload.recommendation_id,
            recommendation_snapshot=payload.recommendation_snapshot,
            title=payload.title.strip(),
            description=payload.description,
            priority=payload.priority,
            status=WorkOrderStatus.OPEN.value,
            assigned_technician_id=payload.assigned_technician_id,
            source=payload.source,
            created_by=payload.created_by,
            due_at=payload.due_at,
        )
        self._work_orders.save(work_order)
        return self._to_out(work_order, include_events=True)

    def get_work_order(self, work_order_id: str) -> WorkOrderOut:
        work_order = self._require_work_order(work_order_id)
        return self._to_out(work_order, include_events=True)

    def list_work_orders(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
        asset_id: str | None = None,
        zone_id: str | None = None,
        utility: str | None = None,
        assigned_technician_id: str | None = None,
    ) -> list[WorkOrderOut]:
        if status:
            self._validate_status_value(status)
        if priority:
            self._validate_priority(priority)

        rows = self._work_orders.list(
            status=status,
            priority=priority,
            asset_id=asset_id,
            zone_id=normalize_zone_id(zone_id),
            utility=normalize_utility_kind(utility),
            assigned_technician_id=assigned_technician_id,
        )
        return [self._to_out(row, include_events=False) for row in rows]

    def update_work_order(self, work_order_id: str, payload: WorkOrderUpdate) -> WorkOrderOut:
        work_order = self._require_work_order(work_order_id)
        if work_order.status == WorkOrderStatus.CLOSED.value:
            raise ValidationAppError("Closed work orders cannot be updated")

        if payload.title is not None:
            work_order.title = payload.title.strip()
        if payload.description is not None:
            work_order.description = payload.description
        if payload.priority is not None:
            self._validate_priority(payload.priority)
            work_order.priority = payload.priority
        if payload.due_at is not None:
            work_order.due_at = payload.due_at
        if payload.assigned_technician_id is not None:
            if payload.assigned_technician_id:
                self._require_technician_for_utility(
                    payload.assigned_technician_id, work_order.utility
                )
            work_order.assigned_technician_id = payload.assigned_technician_id or None

        self._work_orders.save(work_order)
        return self._to_out(work_order, include_events=True)

    def update_work_order_status(
        self,
        work_order_id: str,
        payload: WorkOrderStatusUpdate,
    ) -> WorkOrderOut:
        work_order = self._require_work_order(work_order_id)
        current = work_order.status
        target = payload.status

        self._validate_status_value(target)
        allowed = ALLOWED_STATUS_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValidationAppError(
                f"Invalid status transition: {current} → {target}. "
                f"Allowed from {current}: {', '.join(sorted(allowed)) or 'none'}."
            )

        if target == WorkOrderStatus.ASSIGNED.value:
            technician_id = payload.assigned_technician_id or work_order.assigned_technician_id
            if not technician_id:
                raise ValidationAppError(
                    "assigned_technician_id is required when transitioning to assigned"
                )
            self._require_technician_for_utility(technician_id, work_order.utility)
            work_order.assigned_technician_id = technician_id

        if payload.assigned_technician_id and target != WorkOrderStatus.ASSIGNED.value:
            self._require_technician_for_utility(
                payload.assigned_technician_id, work_order.utility
            )
            work_order.assigned_technician_id = payload.assigned_technician_id

        if target == WorkOrderStatus.IN_PROGRESS.value and work_order.started_at is None:
            work_order.started_at = _utc_now()

        if target == WorkOrderStatus.COMPLETED.value:
            work_order.completed_at = _utc_now()
            if payload.completion_notes is not None:
                work_order.completion_notes = payload.completion_notes

        if target == WorkOrderStatus.CLOSED.value:
            work_order.closed_at = _utc_now()
            if payload.verification_notes is not None:
                work_order.verification_notes = payload.verification_notes

        work_order.status = target
        event = WorkOrderEvent(
            id=_new_event_id(),
            work_order_id=work_order.id,
            from_status=current,
            to_status=target,
            actor=payload.actor,
            notes=payload.notes,
        )
        self._events.add(event)
        self._work_orders.save(work_order)
        return self._to_out(work_order, include_events=True)

    def _require_work_order(self, work_order_id: str) -> WorkOrder:
        work_order = self._work_orders.get(work_order_id)
        if work_order is None:
            raise NotFoundError(f"Work order not found: {work_order_id}")
        return work_order

    def _require_technician(self, technician_id: str) -> None:
        if self._technicians.get(technician_id) is None:
            raise NotFoundError(f"Technician not found: {technician_id}")

    def _require_technician_for_utility(self, technician_id: str, utility: str) -> None:
        technician = self._technicians.get(technician_id)
        if technician is None:
            raise NotFoundError(f"Technician not found: {technician_id}")
        if not technician_qualified_for_utility(technician, utility):
            kinds = [str(k) for k in (technician.utility_kinds or [])] or ["water"]
            raise ValidationAppError(
                f"Technician {technician_id} is not qualified for utility '{utility}'. "
                f"Qualified domains: {', '.join(kinds)}."
            )

    @staticmethod
    def _is_synthetic_scope_asset_id(asset_id: str) -> bool:
        raw = (asset_id or "").strip().lower()
        if not raw:
            return False
        return raw.startswith(
            ("weather-scope:", "environment-scope:", "resilience-scope:")
        )

    @staticmethod
    def _validate_priority(priority: str) -> None:
        try:
            Severity(priority)
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid priority: {priority}. "
                f"Expected one of: {', '.join(s.value for s in Severity)}."
            ) from exc

    @staticmethod
    def _validate_source(source: str) -> None:
        try:
            WorkOrderSource(source)
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid source: {source}. "
                f"Expected one of: {', '.join(s.value for s in WorkOrderSource)}."
            ) from exc

    @staticmethod
    def _validate_status_value(status: str) -> None:
        try:
            WorkOrderStatus(status)
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid status: {status}. "
                f"Expected one of: {', '.join(s.value for s in WorkOrderStatus)}."
            ) from exc

    @staticmethod
    def _to_out(work_order: WorkOrder, *, include_events: bool) -> WorkOrderOut:
        events: list[WorkOrderEventOut] = []
        if include_events:
            events = [
                WorkOrderEventOut(
                    id=event.id,
                    work_order_id=event.work_order_id,
                    from_status=event.from_status,
                    to_status=event.to_status,
                    actor=event.actor,
                    notes=event.notes,
                    created_at=event.created_at,
                )
                for event in work_order.events
            ]
        return WorkOrderOut(
            id=work_order.id,
            asset_id=work_order.asset_id,
            utility=work_order.utility,
            zone_id=work_order.zone_id,
            recommendation_id=work_order.recommendation_id,
            recommendation_snapshot=work_order.recommendation_snapshot,
            title=work_order.title,
            description=work_order.description,
            priority=work_order.priority,
            status=work_order.status,
            assigned_technician_id=work_order.assigned_technician_id,
            source=work_order.source,
            created_by=work_order.created_by,
            created_at=work_order.created_at,
            due_at=work_order.due_at,
            started_at=work_order.started_at,
            completed_at=work_order.completed_at,
            verified_at=work_order.verified_at,
            closed_at=work_order.closed_at,
            completion_notes=work_order.completion_notes,
            verification_notes=work_order.verification_notes,
            events=events,
        )
