"""Water utility plugin — reference implementation for future utilities."""

from __future__ import annotations

from typing import Any

from app.domain.enums import UtilityKind
from app.utilities.base import UtilityPlugin
from app.utilities.water import metrics as water_metrics


class WaterPlugin(UtilityPlugin):
    """First concrete utility plugin (Water)."""

    @property
    def kind(self) -> str:
        return UtilityKind.WATER.value

    @property
    def display_name(self) -> str:
        return "Water"

    def metric_catalog(self) -> list[dict[str, Any]]:
        return water_metrics.catalog()

    def asset_types(self) -> list[str]:
        return list(water_metrics.ASSET_TYPE_METRICS.keys())

    def validate_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        # Sprint 2: pass-through; stricter validation can land later.
        return dict(profile)

    def process_telemetry(self, envelope: dict[str, Any]) -> None:
        # Hook reserved for threshold / health updates — no rules in Sprint 2.
        return None
