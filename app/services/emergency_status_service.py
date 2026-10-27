"""
Emergency status composition (Phase 14.14).

Answers: "Is there an active emergency event right now?"

Qualification is documented and conservative. Does NOT invent emergencies from
warnings alone. Does NOT create work orders or assign technicians.
Reuses existing incidents, alerts, assets, recommendations, and work orders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas.emergency import (
    EmergencyContextOut,
    EmergencyEventOut,
    EmergencyQualificationOut,
    EmergencyStatusOut,
)
from app.domain.enums import (
    AlertStatus,
    IncidentStatus,
    WorkOrderStatus,
)
from app.domain.repositories import (
    AlertRepository,
    AssetRepository,
    WorkOrderRepository,
)
from app.domain.repositories.incident_repository import IncidentRepository
from app.services.dashboard_service import DashboardService

_NOTE = (
    "Emergency status is derived from existing incidents and qualifying critical "
    "alerts joined to asset condition. Medium/low alerts never auto-qualify. "
    "Operators remain responsible for declaring incidents and assigning technicians."
)

_QUALIFICATION_RULES = [
    "Rule A — Operator-declared: an incident with severity high or critical and "
    "status in {open, acknowledged, in_progress} qualifies as an active emergency.",
    "Rule B — Critical alert + asset distress: an open/acknowledged alert with "
    "severity=critical on an asset that is offline, risk=critical, OR health < 40 "
    "qualifies. High/medium/low alerts alone do not qualify.",
    "Warnings, degraded-only assets, and medium alerts never invent an emergency event.",
    "This endpoint never creates work orders or assigns technicians.",
]

_ACTIVE_INCIDENT = {
    IncidentStatus.OPEN.value,
    IncidentStatus.ACKNOWLEDGED.value,
    IncidentStatus.IN_PROGRESS.value,
}

_ACTIVE_ALERT = {AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value}

_ACTIVE_WO = {
    WorkOrderStatus.OPEN.value,
    WorkOrderStatus.ASSIGNED.value,
    WorkOrderStatus.IN_PROGRESS.value,
}


class EmergencyStatusService:
    """Compose emergency qualification from existing operational tables."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._incidents = IncidentRepository(db)
        self._alerts = AlertRepository(db)
        self._assets = AssetRepository(db)
        self._work_orders = WorkOrderRepository(db)
        self._dashboard = DashboardService(db)

    def get_status(self) -> EmergencyStatusOut:
        now = datetime.now(timezone.utc)
        assets_by_id = {a.id: a for a in self._assets.list()}
        events: list[EmergencyEventOut] = []

        # Rule A — declared incidents
        for incident in self._incidents.list():
            if incident.status not in _ACTIVE_INCIDENT:
                continue
            if (incident.severity or "").lower() not in {"high", "critical"}:
                continue
            asset_ids = [incident.asset_id] if incident.asset_id else []
            asset = assets_by_id.get(incident.asset_id) if incident.asset_id else None
            wo = self._latest_wo_for_asset(incident.asset_id) if incident.asset_id else None
            snap = incident.recommendation_snapshot or {}
            events.append(
                EmergencyEventOut(
                    event_id=f"incident:{incident.id}",
                    source_kind="incident",
                    severity=incident.severity,
                    zone_id=incident.zone_id or (asset.zone_id if asset else None),
                    asset_ids=asset_ids,
                    title=incident.title,
                    status=incident.status,
                    incident_id=incident.id,
                    condition=asset.status if asset else None,
                    predictive_risk=asset.risk if asset else None,
                    recommendation_id=incident.recommendation_id,
                    recommendation_title=(
                        str(snap.get("title")) if isinstance(snap, dict) and snap.get("title") else None
                    ),
                    work_order_id=wo["id"] if wo else None,
                    work_order_status=wo["status"] if wo else None,
                    assigned_technician_id=wo.get("assigned_technician_id") if wo else None,
                    explanation=(
                        f"Operator-declared incident {incident.id} "
                        f"(severity={incident.severity}, status={incident.status}) "
                        "qualifies under Rule A."
                    ),
                    details={
                        "utility_kind": incident.utility_kind,
                        "scope": incident.scope,
                        "source": incident.source,
                    },
                )
            )

        # Rule B — critical alert + distressed asset
        alerts = [
            a
            for a in self._alerts.list(active_only=True)
            if a.status in _ACTIVE_ALERT and (a.severity or "").lower() == "critical"
        ]
        covered_assets = {aid for e in events for aid in e.asset_ids}
        for alert in alerts:
            if not alert.asset_id:
                continue
            asset = assets_by_id.get(alert.asset_id)
            if asset is None:
                continue
            if not self._asset_distressed(asset):
                continue
            if asset.id in covered_assets:
                continue
            wo = self._latest_wo_for_asset(asset.id)
            events.append(
                EmergencyEventOut(
                    event_id=f"alert:{alert.id}",
                    source_kind="alert",
                    severity=alert.severity,
                    zone_id=asset.zone_id,
                    asset_ids=[asset.id],
                    title=alert.title,
                    status=alert.status,
                    triggering_alert_id=alert.id,
                    condition=f"{asset.status} · health {float(asset.health):.1f}%",
                    predictive_risk=asset.risk,
                    work_order_id=wo["id"] if wo else None,
                    work_order_status=wo["status"] if wo else None,
                    assigned_technician_id=wo.get("assigned_technician_id") if wo else None,
                    explanation=(
                        f"Critical alert {alert.id} on distressed asset {asset.id} "
                        f"(status={asset.status}, risk={asset.risk}, "
                        f"health={float(asset.health):.1f}) qualifies under Rule B."
                    ),
                    details={
                        "utility_kind": asset.utility_kind,
                        "alert_code": getattr(alert, "alert_code", None),
                    },
                )
            )
            covered_assets.add(asset.id)

        context = self._build_context(assets_by_id)
        has_emergency = bool(events)
        message = (
            "ACTIVE EMERGENCY EVENT DETECTED"
            if has_emergency
            else "NO ACTIVE EMERGENCY EVENT DETECTED"
        )

        return EmergencyStatusOut(
            has_active_emergency=has_emergency,
            message=message,
            qualification=EmergencyQualificationOut(
                summary=(
                    "Emergencies qualify only from high/critical declared incidents "
                    "or critical alerts on distressed assets."
                ),
                rules=_QUALIFICATION_RULES,
            ),
            events=events,
            context=context,
            assessed_at=now,
            note=_NOTE,
        )

    @staticmethod
    def _asset_distressed(asset: Any) -> bool:
        status = (asset.status or "").lower()
        risk = (asset.risk or "").lower()
        health = float(asset.health) if asset.health is not None else 100.0
        return status == "offline" or risk == "critical" or health < 40.0

    def _latest_wo_for_asset(self, asset_id: str | None) -> dict[str, Any] | None:
        if not asset_id:
            return None
        for wo in self._work_orders.list(asset_id=asset_id):
            if wo.status in _ACTIVE_WO:
                return {
                    "id": wo.id,
                    "status": wo.status,
                    "title": wo.title,
                    "assigned_technician_id": wo.assigned_technician_id,
                }
        return None

    def _build_context(self, assets_by_id: dict[str, Any]) -> EmergencyContextOut:
        corridor = self._dashboard.corridor_dashboard()
        degraded = [
            u.utility
            for u in corridor.utilities
            if u.posture in {"DEGRADED", "CRITICAL"} and u.available
        ]
        alerts = [
            a
            for a in self._alerts.list(active_only=True)
            if a.status in _ACTIVE_ALERT
        ]
        critical_alerts = [a for a in alerts if (a.severity or "").lower() == "critical"]
        high_alerts = [a for a in alerts if (a.severity or "").lower() == "high"]

        critical_assets = [
            {
                "asset_id": a.id,
                "asset_name": a.name,
                "utility_kind": a.utility_kind,
                "zone_id": a.zone_id,
                "status": a.status,
                "health": float(a.health) if a.health is not None else None,
                "risk": a.risk,
            }
            for a in assets_by_id.values()
            if (a.status or "").lower() == "offline"
            or (a.risk or "").lower() == "critical"
            or (a.health is not None and float(a.health) < 40.0)
        ][:12]

        active_wos = [
            {
                "id": wo.id,
                "title": wo.title,
                "status": wo.status,
                "asset_id": wo.asset_id,
                "priority": wo.priority,
                "assigned_technician_id": wo.assigned_technician_id,
            }
            for wo in self._work_orders.list()
            if wo.status in _ACTIVE_WO
        ][:12]

        open_sample = [
            {
                "id": a.id,
                "title": a.title,
                "severity": a.severity,
                "asset_id": a.asset_id,
                "status": a.status,
            }
            for a in alerts[:10]
        ]

        return EmergencyContextOut(
            active_alert_count=len(alerts),
            critical_alert_count=len(critical_alerts),
            high_alert_count=len(high_alerts),
            degraded_utilities=degraded,
            critical_assets=critical_assets,
            active_work_orders=active_wos,
            open_alerts_sample=open_sample,
        )
