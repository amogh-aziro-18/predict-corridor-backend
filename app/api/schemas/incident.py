"""Incident API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import IncidentScope, IncidentSource, IncidentStatus


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    severity: str = Field(description="critical | high | medium | low")
    utility_kind: str | None = None
    scope: str = Field(description="corridor | zone | asset")
    zone_id: str | None = None
    asset_id: str | None = None
    source: str = Field(default=IncidentSource.MANUAL.value)
    recommendation_id: str | None = None
    recommendation_snapshot: dict[str, Any] | None = None
    declared_by: str | None = None


class IncidentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class IncidentStatusUpdate(BaseModel):
    status: str = Field(description="Target lifecycle status")
    actor: str | None = None
    notes: str | None = None


class IncidentEventOut(BaseModel):
    id: str
    incident_id: str
    from_status: str | None
    to_status: str
    actor: str | None
    notes: str | None
    created_at: datetime


class IncidentOut(BaseModel):
    id: str
    title: str
    description: str | None
    severity: str
    status: str
    utility_kind: str
    scope: str
    zone_id: str | None
    asset_id: str | None
    source: str
    recommendation_id: str | None
    recommendation_snapshot: dict[str, Any] | None
    declared_by: str | None
    created_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    events: list[IncidentEventOut] = Field(default_factory=list)


ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    IncidentStatus.OPEN.value: {
        IncidentStatus.ACKNOWLEDGED.value,
        IncidentStatus.CLOSED.value,
    },
    IncidentStatus.ACKNOWLEDGED.value: {IncidentStatus.IN_PROGRESS.value},
    IncidentStatus.IN_PROGRESS.value: {IncidentStatus.RESOLVED.value},
    IncidentStatus.RESOLVED.value: {IncidentStatus.CLOSED.value},
    IncidentStatus.CLOSED.value: set(),
}

# Platform severity for incidents excludes info / resilience none.
INCIDENT_SEVERITIES = frozenset({"critical", "high", "medium", "low"})

# Re-export scopes/sources for consumers
__all__ = [
    "IncidentCreate",
    "IncidentUpdate",
    "IncidentStatusUpdate",
    "IncidentEventOut",
    "IncidentOut",
    "ALLOWED_STATUS_TRANSITIONS",
    "INCIDENT_SEVERITIES",
    "IncidentScope",
    "IncidentSource",
    "IncidentStatus",
]
