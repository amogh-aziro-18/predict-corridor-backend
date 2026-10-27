"""
Power → Alert producer (Phase 14.3B).

Translates Power evaluate_alerts() codes into persisted Alerts via AlertService.
Set-diff sync prevents per-tick spam.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.enums import AlertScope, AlertSource
from app.domain.models import Asset
from app.services.alert_service import AlertService

_POWER_CODE_META: dict[str, tuple[str, str, str]] = {
    "load_elevated": (
        "Elevated electrical loading",
        "Rule-based assessment: load percentage above warning band.",
        "medium",
    ),
    "load_high": (
        "High electrical loading",
        "Rule-based assessment: sustained high load percentage.",
        "high",
    ),
    "load_critical": (
        "Critical electrical loading",
        "Rule-based assessment: load percentage at or above critical band.",
        "critical",
    ),
    "load_rising": (
        "Rising load trend",
        "Rule-based assessment: load percentage rising across recent samples while elevated.",
        "medium",
    ),
    "oil_temp_elevated": (
        "Elevated transformer oil temperature",
        "Rule-based assessment: oil temperature above warning band.",
        "medium",
    ),
    "oil_temp_high": (
        "High transformer oil temperature",
        "Rule-based assessment: oil temperature above high band.",
        "high",
    ),
    "oil_temp_critical": (
        "Critical transformer oil temperature",
        "Rule-based assessment: oil temperature at or above critical band.",
        "critical",
    ),
    "oil_temp_rising": (
        "Rising oil temperature trend",
        "Rule-based assessment: oil temperature rising across recent samples while elevated.",
        "medium",
    ),
    "voltage_low": (
        "Low voltage",
        "Rule-based assessment: voltage below operational low band.",
        "high",
    ),
    "voltage_critical_low": (
        "Critical low voltage",
        "Rule-based assessment: voltage at or below critical low band.",
        "critical",
    ),
    "power_factor_low": (
        "Low power factor",
        "Rule-based assessment: power factor below warning band.",
        "medium",
    ),
    "power_factor_critical": (
        "Critical power factor",
        "Rule-based assessment: power factor at or below critical band.",
        "high",
    ),
    "frequency_deviation": (
        "Frequency deviation",
        "Rule-based assessment: frequency outside normal operating band.",
        "high",
    ),
    "thermal_loading_stress": (
        "Transformer thermal and loading stress",
        "Rule-based assessment: combined elevated oil temperature and loading with trend evidence.",
        "high",
    ),
}


def power_alert_meta(code: str) -> tuple[str, str | None, str]:
    if code in _POWER_CODE_META:
        title, description, severity = _POWER_CODE_META[code]
        return title, description, severity
    return code.replace("_", " ").title(), f"Power condition code: {code}", "medium"


def known_power_alert_codes() -> frozenset[str]:
    return frozenset(_POWER_CODE_META.keys())


def sync_power_asset_alerts(db: Session, *, asset: Asset, codes: list[str]) -> None:
    present = {c for c in codes if c in _POWER_CODE_META}
    AlertService(db).sync_condition_set(
        source=AlertSource.TELEMETRY.value,
        utility_kind=asset.utility_kind,
        scope=AlertScope.ASSET.value,
        zone_id=asset.zone_id,
        asset_id=asset.id,
        present_codes=present,
        meta_for_code=power_alert_meta,
        resolve_notes="Power threshold condition cleared",
    )
