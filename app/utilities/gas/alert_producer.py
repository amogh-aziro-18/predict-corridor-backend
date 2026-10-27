"""
Gas → Alert producer (Phase 15.1–15.2).

Translates Gas evaluate_alerts() codes into persisted Alerts via AlertService.
Set-diff sync prevents per-tick spam; clearing a code resolves the open alert.

Severity mapping (matches existing Alert severity vocabulary):
- ``*_elevated`` / ``*_rising`` / early warnings → medium
- ``*_high`` / ``compressor_mechanical_stress`` → high
- ``*_critical`` → critical
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.enums import AlertScope, AlertSource
from app.domain.models import Asset
from app.services.alert_service import AlertService

_GAS_CODE_META: dict[str, tuple[str, str, str]] = {
    "compressor_load_elevated": (
        "Elevated compressor load",
        "Rule-based assessment: compressor load percentage above warning band.",
        "medium",
    ),
    "compressor_load_high": (
        "High compressor load",
        "Rule-based assessment: sustained high compressor load percentage.",
        "high",
    ),
    "compressor_load_critical": (
        "Critical compressor load",
        "Rule-based assessment: compressor load at or above critical band.",
        "critical",
    ),
    "compressor_load_rising": (
        "Rising compressor load trend",
        "Rule-based assessment: load rising across recent samples while elevated.",
        "medium",
    ),
    "vibration_elevated": (
        "Elevated vibration",
        "Rule-based assessment: vibration above warning band.",
        "medium",
    ),
    "vibration_high": (
        "High vibration",
        "Rule-based assessment: vibration above high band.",
        "high",
    ),
    "vibration_critical": (
        "Critical vibration",
        "Rule-based assessment: vibration at or above critical band.",
        "critical",
    ),
    "vibration_rising": (
        "Rising vibration trend",
        "Rule-based assessment: vibration rising across recent samples while elevated.",
        "medium",
    ),
    "temperature_elevated": (
        "Elevated gas/process temperature",
        "Rule-based assessment: temperature above warning band.",
        "medium",
    ),
    "temperature_high": (
        "High temperature",
        "Rule-based assessment: temperature above high band.",
        "high",
    ),
    "temperature_critical": (
        "Critical temperature",
        "Rule-based assessment: temperature at or above critical band.",
        "critical",
    ),
    "temperature_rising": (
        "Rising temperature trend",
        "Rule-based assessment: temperature rising across recent samples while elevated.",
        "medium",
    ),
    "leak_indicator_elevated": (
        "Elevated leak indicator (POC)",
        "Rule-based assessment: synthetic leak indicator above warning band.",
        "medium",
    ),
    "leak_indicator_high": (
        "High leak indicator (POC)",
        "Rule-based assessment: synthetic leak indicator above high band.",
        "high",
    ),
    "leak_indicator_critical": (
        "Critical leak indicator (POC)",
        "Rule-based assessment: synthetic leak indicator at or above critical band.",
        "critical",
    ),
    "leak_indicator_rising": (
        "Rising leak indicator trend (POC)",
        "Rule-based assessment: leak indicator rising while elevated.",
        "medium",
    ),
    "gas_quality_elevated": (
        "Degraded gas quality",
        "Rule-based assessment: gas quality percentage below warning band.",
        "medium",
    ),
    "gas_quality_high": (
        "Poor gas quality",
        "Rule-based assessment: gas quality percentage below high band.",
        "high",
    ),
    "gas_quality_critical": (
        "Critical gas quality",
        "Rule-based assessment: gas quality percentage at or below critical band.",
        "critical",
    ),
    "pressure_elevated": (
        "Pressure deviation from nominal",
        "Rule-based assessment: pressure deviation from profile nominal above warning band.",
        "medium",
    ),
    "pressure_high": (
        "High pressure deviation from nominal",
        "Rule-based assessment: pressure deviation from profile nominal above high band.",
        "high",
    ),
    "pressure_critical": (
        "Critical pressure deviation from nominal",
        "Rule-based assessment: pressure deviation from profile nominal at critical band.",
        "critical",
    ),
    "flow_elevated": (
        "Flow deviation from nominal",
        "Rule-based assessment: flow deviation from profile nominal above warning band.",
        "medium",
    ),
    "flow_high": (
        "High flow deviation from nominal",
        "Rule-based assessment: flow deviation from profile nominal above high band.",
        "high",
    ),
    "flow_critical": (
        "Critical flow deviation from nominal",
        "Rule-based assessment: flow deviation from profile nominal at critical band.",
        "critical",
    ),
    "discharge_pressure_elevated": (
        "Discharge pressure deviation",
        "Rule-based assessment: compressor discharge pressure deviation from nominal.",
        "medium",
    ),
    "discharge_pressure_high": (
        "High discharge pressure deviation",
        "Rule-based assessment: compressor discharge pressure high deviation from nominal.",
        "high",
    ),
    "discharge_pressure_critical": (
        "Critical discharge pressure deviation",
        "Rule-based assessment: compressor discharge pressure critical deviation from nominal.",
        "critical",
    ),
    "power_consumption_elevated": (
        "Power consumption deviation",
        "Rule-based assessment: compressor power consumption deviation from nominal.",
        "medium",
    ),
    "power_consumption_high": (
        "High power consumption deviation",
        "Rule-based assessment: compressor power consumption high deviation from nominal.",
        "high",
    ),
    "power_consumption_critical": (
        "Critical power consumption deviation",
        "Rule-based assessment: compressor power consumption critical deviation from nominal.",
        "critical",
    ),
    "valve_position_elevated": (
        "Valve position near band edge",
        "Rule-based assessment: regulator valve position near operating band edge.",
        "medium",
    ),
    "valve_position_high": (
        "Valve position extreme",
        "Rule-based assessment: regulator valve position at extreme operating band.",
        "high",
    ),
    "compressor_mechanical_stress": (
        "Combined compressor load and vibration stress",
        "Rule-based assessment: elevated compressor load and vibration with trend/stress evidence.",
        "high",
    ),
}


def gas_alert_meta(code: str) -> tuple[str, str | None, str]:
    if code in _GAS_CODE_META:
        title, description, severity = _GAS_CODE_META[code]
        return title, description, severity
    return code.replace("_", " ").title(), f"Gas condition code: {code}", "medium"


def known_gas_alert_codes() -> frozenset[str]:
    return frozenset(_GAS_CODE_META.keys())


def sync_gas_asset_alerts(db: Session, *, asset: Asset, codes: list[str]) -> None:
    present = {c for c in codes if c in _GAS_CODE_META}
    AlertService(db).sync_condition_set(
        source=AlertSource.TELEMETRY.value,
        utility_kind=asset.utility_kind,
        scope=AlertScope.ASSET.value,
        zone_id=asset.zone_id,
        asset_id=asset.id,
        present_codes=present,
        meta_for_code=gas_alert_meta,
        resolve_notes="Gas threshold condition cleared",
    )
