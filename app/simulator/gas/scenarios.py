"""Gas simulator zone scenario modes — telemetry trend drivers only (no health/alerts)."""

from __future__ import annotations

from enum import StrEnum


class GasScenarioMode(StrEnum):
    """CLI / process-level scenario selection."""

    NORMAL = "normal"
    DEGRADING = "degrading"
    MIXED = "mixed"


class ZoneGasBehavior(StrEnum):
    """Per-zone telemetry behavior for the Gas simulator."""

    NORMAL = "normal"
    DEGRADING = "degrading"
    STRONG_DEGRADING = "strong_degrading"


# Demo-friendly four-zone mapping for MIXED mode.
MIXED_ZONE_BEHAVIOR: dict[str, ZoneGasBehavior] = {
    "zone-a": ZoneGasBehavior.NORMAL,
    "zone-b": ZoneGasBehavior.NORMAL,
    "zone-c": ZoneGasBehavior.DEGRADING,
    "zone-d": ZoneGasBehavior.STRONG_DEGRADING,
}


def resolve_zone_behavior(mode: GasScenarioMode, zone_id: str | None) -> ZoneGasBehavior:
    """Map process mode + zone id to per-zone telemetry behavior."""
    if mode == GasScenarioMode.NORMAL:
        return ZoneGasBehavior.NORMAL
    if mode == GasScenarioMode.DEGRADING:
        return ZoneGasBehavior.DEGRADING
    if zone_id and zone_id in MIXED_ZONE_BEHAVIOR:
        return MIXED_ZONE_BEHAVIOR[zone_id]
    return ZoneGasBehavior.NORMAL


def degradation_increment(behavior: ZoneGasBehavior) -> float:
    """
    Per-tick intensity step for degrading zones.

    Accumulated intensity (0→1) drives gradual metric drift — not a status flag.
    """
    if behavior == ZoneGasBehavior.DEGRADING:
        return 0.035
    if behavior == ZoneGasBehavior.STRONG_DEGRADING:
        return 0.065
    return 0.0
