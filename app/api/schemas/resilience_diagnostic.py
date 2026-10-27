"""
Corridor resilience diagnostic schemas (Phase 14.14).

Operator-facing categorical postures only — not a numeric resilience score.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DiagnosticSliceOut(BaseModel):
    """One diagnostic facet (utility / environment / weather / operations)."""

    id: str
    label: str
    posture: str = Field(description="HEALTHY | STABLE | AT RISK | CRITICAL | UNAVAILABLE")
    kind: str = Field(
        description="operational | environmental | predictive | weather | operations",
    )
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


class ResilienceDiagnosticOut(BaseModel):
    """
    Corridor-level operational diagnostic for the Resilience page.

    Composes existing dashboard, resilience assessment, alerts, work orders,
    and asset registry fields. Does not invent a 0–100 resilience score.
    """

    overall_posture: str = Field(
        description="HEALTHY | STABLE | AT RISK | CRITICAL (mapped from live signals)",
    )
    why: str
    why_bullets: list[str] = Field(default_factory=list)
    slices: list[DiagnosticSliceOut] = Field(default_factory=list)
    environmental_context_posture: str | None = None
    weather_context_posture: str | None = None
    operational_posture: str | None = None
    predictive_notes: list[str] = Field(default_factory=list)
    assessed_at: datetime
    note: str
