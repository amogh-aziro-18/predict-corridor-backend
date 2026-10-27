"""Communications simulator zone scenario modes — telemetry trend drivers only (no health/alerts)."""

from __future__ import annotations

from enum import StrEnum


class CommunicationsScenarioMode(StrEnum):
    """CLI / process-level scenario selection."""

    NORMAL = "normal"
    DEGRADING = "degrading"
    MIXED = "mixed"


class ZoneCommunicationsBehavior(StrEnum):
    """Per-zone telemetry behavior for the Communications simulator."""

    NORMAL = "normal"
    DEGRADING = "degrading"
    STRONG_DEGRADING = "strong_degrading"


# Demo-friendly four-zone mapping for MIXED mode (Gas-style corridor parity).
# POC ASSUMPTION — four zones are corridor demo topology, not a verified telecom mesh.
MIXED_ZONE_BEHAVIOR: dict[str, ZoneCommunicationsBehavior] = {
    "zone-a": ZoneCommunicationsBehavior.NORMAL,
    "zone-b": ZoneCommunicationsBehavior.NORMAL,
    "zone-c": ZoneCommunicationsBehavior.DEGRADING,
    "zone-d": ZoneCommunicationsBehavior.STRONG_DEGRADING,
}


def resolve_zone_behavior(
    mode: CommunicationsScenarioMode, zone_id: str | None
) -> ZoneCommunicationsBehavior:
    """Map process mode + zone id to per-zone telemetry behavior."""
    if mode == CommunicationsScenarioMode.NORMAL:
        return ZoneCommunicationsBehavior.NORMAL
    if mode == CommunicationsScenarioMode.DEGRADING:
        return ZoneCommunicationsBehavior.DEGRADING
    if zone_id and zone_id in MIXED_ZONE_BEHAVIOR:
        return MIXED_ZONE_BEHAVIOR[zone_id]
    return ZoneCommunicationsBehavior.NORMAL


def degradation_increment(behavior: ZoneCommunicationsBehavior) -> float:
    """
    Per-tick intensity step for degrading zones.

    Accumulated intensity (0→1) drives gradual metric drift and sticky outage
    entry — not a health status flag (health is Phase 17.4).
    """
    if behavior == ZoneCommunicationsBehavior.DEGRADING:
        return 0.035
    if behavior == ZoneCommunicationsBehavior.STRONG_DEGRADING:
        return 0.065
    return 0.0
