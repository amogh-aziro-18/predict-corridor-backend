"""
Water → Alert producer (Phase 12).

Translates existing Water evaluate_alerts() codes into persisted Alerts.
Does not rewrite thresholds — only maps known codes to Alert fields.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.enums import AlertScope, AlertSource
from app.domain.models import Asset
from app.services.alert_service import AlertService

# Actual codes from app.utilities.water.health.evaluate_alerts
_WATER_CODE_META: dict[str, tuple[str, str, str]] = {
    "pressure_critical_low": (
        "Critical low pressure",
        "Water pressure at or below critical low threshold.",
        "critical",
    ),
    "pressure_low": (
        "Low pressure",
        "Water pressure below operational low threshold.",
        "high",
    ),
    "pressure_high": (
        "High pressure",
        "Water pressure above operational high threshold.",
        "high",
    ),
    "level_critical": (
        "Critical tank/reservoir level",
        "Water level at or below critical threshold.",
        "critical",
    ),
    "level_low": (
        "Low tank/reservoir level",
        "Water level below operational low threshold.",
        "high",
    ),
    "flow_low": (
        "Low flow rate",
        "Flow rate below operational low threshold.",
        "high",
    ),
    "pump_stopped": (
        "Pump stopped",
        "Pump running state indicates unavailable / stopped.",
        "high",
    ),
    "turbidity_high": (
        "High turbidity",
        "Turbidity above operational threshold.",
        "medium",
    ),
}


def water_alert_meta(code: str) -> tuple[str, str | None, str]:
    if code in _WATER_CODE_META:
        title, description, severity = _WATER_CODE_META[code]
        return title, description, severity
    # Unknown code: do not invent severity inflation — skip via caller filter
    return code.replace("_", " ").title(), f"Water condition code: {code}", "medium"


def known_water_alert_codes() -> frozenset[str]:
    return frozenset(_WATER_CODE_META.keys())


def sync_water_asset_alerts(db: Session, *, asset: Asset, codes: list[str]) -> None:
    """
    Set-diff sync for one Water asset after evaluate_alerts().

    Only maps known Water codes; ignores unknown codes.
    """
    present = {c for c in codes if c in _WATER_CODE_META}
    AlertService(db).sync_condition_set(
        source=AlertSource.TELEMETRY.value,
        utility_kind=asset.utility_kind,
        scope=AlertScope.ASSET.value,
        zone_id=asset.zone_id,
        asset_id=asset.id,
        present_codes=present,
        meta_for_code=water_alert_meta,
        resolve_notes="Water threshold condition cleared",
    )
