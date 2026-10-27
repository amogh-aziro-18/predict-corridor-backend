"""Technician API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TechnicianOut(BaseModel):
    id: str
    name: str
    role: str
    primary_zone_id: str | None
    skills: list[Any]
    utility_kinds: list[str] = Field(default_factory=list)
    availability: str
    emergency_certified: bool
    created_at: datetime
    updated_at: datetime
