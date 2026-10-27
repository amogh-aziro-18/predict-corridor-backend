"""Emergency status schemas (Phase 14.14)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EmergencyQualificationOut(BaseModel):
    """Documented qualification rules — not a second alert system."""

    summary: str
    rules: list[str]


class EmergencyEventOut(BaseModel):
    """One qualifying emergency event derived from existing incidents or alerts."""

    event_id: str
    source_kind: str = Field(description="incident | alert")
    severity: str
    zone_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    title: str
    status: str | None = None
    triggering_alert_id: str | None = None
    incident_id: str | None = None
    condition: str | None = None
    predictive_risk: str | None = None
    recommendation_id: str | None = None
    recommendation_title: str | None = None
    work_order_id: str | None = None
    work_order_status: str | None = None
    assigned_technician_id: str | None = None
    explanation: str
    details: dict[str, Any] = Field(default_factory=dict)


class EmergencyContextOut(BaseModel):
    """Useful non-emergency operational context when no event qualifies."""

    active_alert_count: int = 0
    critical_alert_count: int = 0
    high_alert_count: int = 0
    degraded_utilities: list[str] = Field(default_factory=list)
    critical_assets: list[dict[str, Any]] = Field(default_factory=list)
    active_work_orders: list[dict[str, Any]] = Field(default_factory=list)
    open_alerts_sample: list[dict[str, Any]] = Field(default_factory=list)


class EmergencyStatusOut(BaseModel):
    has_active_emergency: bool
    message: str
    qualification: EmergencyQualificationOut
    events: list[EmergencyEventOut] = Field(default_factory=list)
    context: EmergencyContextOut
    assessed_at: datetime
    note: str
