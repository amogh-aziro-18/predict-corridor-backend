"""Agent workflow response schemas (Sprint 7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OperationalSummaryOut(BaseModel):
    """Structured output of the LangGraph operational-summary workflow."""

    telemetry: dict[str, Any] = Field(default_factory=dict)
    forecast: dict[str, Any] = Field(default_factory=dict)
    maintenance: dict[str, Any] = Field(default_factory=dict)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    priorities: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    generated_at: datetime
    execution_time_ms: float
    execution_status: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
