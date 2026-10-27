"""Gas utility plugin — Phase 15.0 registration foundation."""

from __future__ import annotations

from typing import Any

from app.domain.enums import UtilityKind
from app.utilities.base import UtilityPlugin
from app.utilities.gas import metrics as gas_metrics


class GasPlugin(UtilityPlugin):
    """Concrete Gas utility plugin (coexists with Water and Power)."""

    @property
    def kind(self) -> str:
        return UtilityKind.GAS.value

    @property
    def display_name(self) -> str:
        return "Gas"

    def metric_catalog(self) -> list[dict[str, Any]]:
        return gas_metrics.catalog()

    def asset_types(self) -> list[str]:
        return list(gas_metrics.ASSET_TYPE_METRICS.keys())

    def validate_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        # Foundation: pass-through. Stricter validation lands with health phases.
        return dict(profile)

    def process_telemetry(self, envelope: dict[str, Any]) -> None:
        # Reserved for Phase 15.1+ threshold / health updates — no scoring yet.
        return None
