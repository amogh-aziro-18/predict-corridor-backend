"""Alert API schemas (Phase 12)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import AlertStatus

ALERT_SEVERITIES = frozenset({"critical", "high", "medium", "low"})

ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    AlertStatus.OPEN.value: {
        AlertStatus.ACKNOWLEDGED.value,
        AlertStatus.RESOLVED.value,
    },
    AlertStatus.ACKNOWLEDGED.value: {AlertStatus.RESOLVED.value},
    AlertStatus.RESOLVED.value: set(),
}


class AlertStatusUpdate(BaseModel):
    status: str = Field(description="acknowledged | resolved")
    notes: str | None = None


class AlertOut(BaseModel):
    id: str
    fingerprint: str
    title: str
    description: str | None
    severity: str
    status: str
    source: str
    alert_code: str
    utility_kind: str
    scope: str
    zone_id: str | None
    asset_id: str | None
    recommendation_id: str | None
    raised_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    ack_notes: str | None = None
    resolve_notes: str | None = None
