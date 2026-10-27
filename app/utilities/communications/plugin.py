"""Communications utility plugin — Phase 17.1 registration foundation."""

from __future__ import annotations

from typing import Any

from app.domain.enums import UtilityKind
from app.utilities.base import UtilityPlugin
from app.utilities.communications import metrics as communications_metrics


class CommunicationsPlugin(UtilityPlugin):
    """Concrete Communications utility plugin (coexists with Water, Power, Gas)."""

    @property
    def kind(self) -> str:
        return UtilityKind.COMMUNICATIONS.value

    @property
    def display_name(self) -> str:
        return "Communications"

    def metric_catalog(self) -> list[dict[str, Any]]:
        return communications_metrics.catalog()

    def asset_types(self) -> list[str]:
        return list(communications_metrics.ASSET_TYPE_METRICS.keys())

    def validate_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        # Foundation: pass-through. Stricter validation lands with health phases.
        return dict(profile)

    def process_telemetry(self, envelope: dict[str, Any]) -> None:
        # Condition assessment is owned by CommunicationsService (Gas/Power parity),
        # invoked from TelemetryService / the Communications simulator — not here.
        return None
