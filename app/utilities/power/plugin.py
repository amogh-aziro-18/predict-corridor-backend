"""Power utility plugin — Phase 14.2 registration foundation."""

from __future__ import annotations

from typing import Any

from app.domain.enums import UtilityKind
from app.utilities.base import UtilityPlugin
from app.utilities.power import metrics as power_metrics


class PowerPlugin(UtilityPlugin):
    """Concrete Power utility plugin (coexists with Water)."""

    @property
    def kind(self) -> str:
        return UtilityKind.POWER.value

    @property
    def display_name(self) -> str:
        return "Power"

    def metric_catalog(self) -> list[dict[str, Any]]:
        return power_metrics.catalog()

    def asset_types(self) -> list[str]:
        return list(power_metrics.ASSET_TYPE_METRICS.keys())

    def validate_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        # Foundation: pass-through. Stricter validation lands with health/sim phases.
        return dict(profile)

    def process_telemetry(self, envelope: dict[str, Any]) -> None:
        # Reserved for Phase 14.3+ threshold / health updates — no scoring yet.
        return None
