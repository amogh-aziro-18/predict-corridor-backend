"""
Communications → Alert producer (Phase 17.5).

Translates Communications evaluate_alerts() codes into persisted Alerts via
AlertService. Set-diff sync prevents per-tick spam; clearing a code resolves
the open alert.

Severity mapping (matches existing Alert severity vocabulary):
- ``*_elevated`` / ``*_rising`` → medium
- ``*_high`` / ``path_quality_stress`` → high
- ``*_critical`` / ``link_down`` → critical

``unavailable`` is a condition vocabulary label — not an alert_code.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.enums import AlertScope, AlertSource
from app.domain.models import Asset
from app.services.alert_service import AlertService

_COMM_CODE_META: dict[str, tuple[str, str, str]] = {
    "rtt_elevated": (
        "Communications latency elevated",
        "Rule-based assessment: link RTT overage vs nominal above warning band.",
        "medium",
    ),
    "rtt_high": (
        "Communications latency high",
        "Rule-based assessment: link RTT overage vs nominal above high band.",
        "high",
    ),
    "rtt_critical": (
        "Communications latency critical",
        "Rule-based assessment: link RTT overage vs nominal at critical band.",
        "critical",
    ),
    "rtt_rising": (
        "Communications latency rising",
        "Rule-based assessment: link RTT rising across recent samples while elevated.",
        "medium",
    ),
    "packet_loss_elevated": (
        "Packet loss elevated",
        "Rule-based assessment: packet loss percentage above warning band.",
        "medium",
    ),
    "packet_loss_high": (
        "Packet loss high",
        "Rule-based assessment: packet loss percentage above high band.",
        "high",
    ),
    "packet_loss_critical": (
        "Packet loss critical",
        "Rule-based assessment: packet loss percentage at or above critical band.",
        "critical",
    ),
    "packet_loss_rising": (
        "Packet loss rising",
        "Rule-based assessment: packet loss rising across recent samples while elevated.",
        "medium",
    ),
    "cabinet_temp_elevated": (
        "Communications cabinet temperature elevated",
        "Rule-based assessment: cabinet temperature above warning band (POC — not NEBS).",
        "medium",
    ),
    "cabinet_temp_high": (
        "Communications cabinet temperature high",
        "Rule-based assessment: cabinet temperature above high band (POC — not NEBS).",
        "high",
    ),
    "cabinet_temp_critical": (
        "Communications cabinet temperature critical",
        "Rule-based assessment: cabinet temperature at or above critical band (POC — not NEBS).",
        "critical",
    ),
    "cabinet_temp_rising": (
        "Communications cabinet temperature rising",
        "Rule-based assessment: cabinet temperature rising while elevated (POC).",
        "medium",
    ),
    "power_draw_elevated": (
        "Communications power draw elevated",
        "Rule-based assessment: power draw deviation from nominal above warning band.",
        "medium",
    ),
    "power_draw_high": (
        "Communications power draw high",
        "Rule-based assessment: power draw deviation from nominal above high band.",
        "high",
    ),
    "path_quality_stress": (
        "Communications path quality degraded",
        "Rule-based assessment: combined elevated RTT and packet loss with trend/high evidence.",
        "high",
    ),
    "link_down": (
        "Communications link unavailable",
        "Rule-based assessment: link_up indicates path down; availability dominates other metrics.",
        "critical",
    ),
}


def communications_alert_meta(code: str) -> tuple[str, str | None, str]:
    if code in _COMM_CODE_META:
        title, description, severity = _COMM_CODE_META[code]
        return title, description, severity
    return code.replace("_", " ").title(), f"Communications condition code: {code}", "medium"


def known_communications_alert_codes() -> frozenset[str]:
    return frozenset(_COMM_CODE_META.keys())


def sync_communications_asset_alerts(db: Session, *, asset: Asset, codes: list[str]) -> None:
    """
    Set-diff sync for one Communications asset after condition evaluation.

    Only maps known Communications finding codes; ignores unknown codes
    (including the condition label ``unavailable``).
    """
    present = {c for c in codes if c in _COMM_CODE_META}
    AlertService(db).sync_condition_set(
        source=AlertSource.TELEMETRY.value,
        utility_kind=asset.utility_kind,
        scope=AlertScope.ASSET.value,
        zone_id=asset.zone_id,
        asset_id=asset.id,
        present_codes=present,
        meta_for_code=communications_alert_meta,
        resolve_notes="Communications threshold condition cleared",
    )
