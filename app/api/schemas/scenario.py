"""Scenario API schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ScenarioOut(BaseModel):
    key: str
    name: str
    description: str
    category: str
    default_severity: str


class CurrentScenarioOut(BaseModel):
    key: str
    name: str
    description: str
    category: str
    default_severity: str
    intensity: float = Field(ge=0.0, le=1.0)
    previous_key: str | None = None
    selected_at: datetime
    updated_at: datetime


class ScenarioSelectOut(BaseModel):
    key: str
    name: str
    message: str
    intensity: float
