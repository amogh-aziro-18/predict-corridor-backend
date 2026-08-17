"""UtilityPlugin protocol — contract every utility module must satisfy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class UtilityPlugin(ABC):
    """
    Plugin boundary for a single utility kind (water, power, gas, ...).

    Sprint 1: structure and placeholders only — no business rules.
    """

    @property
    @abstractmethod
    def kind(self) -> str:
        """Stable utility kind code, e.g. ``water``."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for APIs and UI."""

    @abstractmethod
    def metric_catalog(self) -> list[dict[str, Any]]:
        """
        Declared metric keys and units for this utility.

        Example shape (future): ``{"key": "flow_lps", "unit": "L/s", "label": "Flow"}``.
        """

    @abstractmethod
    def asset_types(self) -> list[str]:
        """Allowed asset ``type`` values for this utility."""

    def validate_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Validate/normalize utility-specific asset profile. Override in plugins."""
        return profile

    def process_telemetry(self, envelope: dict[str, Any]) -> None:
        """
        Hook for utility-specific telemetry interpretation.

        Sprint 1: no-op placeholder — no rules or scoring.
        """
        return None
