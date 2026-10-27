"""
Environment provider abstraction (Phase 9A).

Mirrors the WeatherProvider pattern for future external environmental sources
(e.g. air quality) without coupling to WeatherProvider or implementing any
network/provider logic yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class EnvironmentLocation:
    """Resolved lookup point for external environment providers."""

    latitude: float
    longitude: float
    scope: str  # corridor | zone
    zone_id: str | None
    asset_count: int
    utility_kind: str
    method: str
    timezone: str | None = None
    city: str | None = None
    country: str | None = None
    display_name: str | None = None
    location_note: str | None = None


@dataclass(frozen=True)
class NormalizedEnvironmentObservation:
    """Provider-normalized observation prior to EnvironmentSignal mapping."""

    category: str
    signal_type: str
    measured_value: float
    unit: str
    observation_kind: str  # current | forecast | historical
    observed_at: datetime
    provider: str
    provider_fetched_at: datetime
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class EnvironmentProvider(ABC):
    """Pluggable external environmental observation source."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier (e.g. future open_meteo_air_quality)."""

    @abstractmethod
    def fetch_observations(
        self,
        location: EnvironmentLocation,
        *,
        observation_kind: str = "current",
        hours: int = 0,
    ) -> list[NormalizedEnvironmentObservation]:
        """
        Fetch normalized environmental observations for a resolved location.

        ``observation_kind``: current | forecast | all
        ``hours``: forecast horizon when forecast/all is requested (0 = current only).

        Implementations must not fabricate measurements.
        """


class EnvironmentProviderError(Exception):
    """Raised when an external environment provider call fails."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause
