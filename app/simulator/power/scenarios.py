"""Power simulator zone scenario modes — telemetry trend drivers only (no health/alerts)."""

from __future__ import annotations

from enum import StrEnum


class PowerScenarioMode(StrEnum):
    """CLI / process-level scenario selection."""

    NORMAL = "normal"
    DEGRADING = "degrading"
    MIXED = "mixed"


class ZonePowerBehavior(StrEnum):
    """Per-zone telemetry behavior for the Power simulator."""

    NORMAL = "normal"
    DEGRADING = "degrading"
    STRONG_DEGRADING = "strong_degrading"


# Demo-friendly four-zone mapping for MIXED mode.
MIXED_ZONE_BEHAVIOR: dict[str, ZonePowerBehavior] = {
    "zone-a": ZonePowerBehavior.NORMAL,
    "zone-b": ZonePowerBehavior.NORMAL,
    "zone-c": ZonePowerBehavior.DEGRADING,
    "zone-d": ZonePowerBehavior.STRONG_DEGRADING,
}


def resolve_zone_behavior(mode: PowerScenarioMode, zone_id: str | None) -> ZonePowerBehavior:
    """Map process mode + zone id to per-zone telemetry behavior."""
    if mode == PowerScenarioMode.NORMAL:
        return ZonePowerBehavior.NORMAL
    if mode == PowerScenarioMode.DEGRADING:
        # Uniform mild degradation across all zones (non-demo bulk mode).
        return ZonePowerBehavior.DEGRADING
    # MIXED — A/B normal, C degrading, D stronger.
    if zone_id and zone_id in MIXED_ZONE_BEHAVIOR:
        return MIXED_ZONE_BEHAVIOR[zone_id]
    return ZonePowerBehavior.NORMAL


def degradation_increment(behavior: ZonePowerBehavior) -> float:
    """
    Per-tick intensity step for degrading zones.

    Accumulated intensity (0→1) drives gradual metric drift — not a status flag.
    """
    if behavior == ZonePowerBehavior.DEGRADING:
        return 0.035
    if behavior == ZonePowerBehavior.STRONG_DEGRADING:
        return 0.065
    return 0.0
