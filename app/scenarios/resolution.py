"""Pure helpers for resolving corridor vs per-zone scenario overrides."""

from __future__ import annotations

from typing import Any

from app.scenarios.definitions import ScenarioKey


def normalize_zone_scenarios(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Return a cleaned zone_id → {scenario_key, intensity} mapping."""
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for zone_id, entry in raw.items():
        if not isinstance(zone_id, str) or not zone_id.strip():
            continue
        if not isinstance(entry, dict):
            continue
        key = entry.get("scenario_key")
        if not isinstance(key, str) or not key.strip():
            continue
        try:
            ScenarioKey(key)
        except ValueError:
            continue
        intensity = entry.get("intensity", 0.0)
        try:
            intensity_f = float(intensity)
        except (TypeError, ValueError):
            intensity_f = 0.0
        intensity_f = max(0.0, min(1.0, intensity_f))
        out[zone_id.strip()] = {
            "scenario_key": key,
            "intensity": intensity_f,
        }
    return out


def resolve_effective_scenario(
    *,
    global_key: str,
    global_intensity: float,
    zone_scenarios: dict[str, Any] | None,
    zone_id: str | None,
) -> tuple[ScenarioKey, float]:
    """
    Pick the scenario that applies to one asset zone.

    If ``zone_id`` has an override entry, that key/intensity wins.
    Otherwise the corridor-wide global scenario is used.
    """
    try:
        global_parsed = ScenarioKey(global_key)
    except ValueError:
        global_parsed = ScenarioKey.NORMAL

    global_intensity = max(0.0, min(1.0, float(global_intensity)))
    zones = normalize_zone_scenarios(zone_scenarios)
    if zone_id and zone_id in zones:
        entry = zones[zone_id]
        try:
            return ScenarioKey(entry["scenario_key"]), float(entry["intensity"])
        except (ValueError, KeyError, TypeError):
            return global_parsed, global_intensity
    return global_parsed, global_intensity


# Documented demo mapping: A/B normal, C degraded, D critical — using existing keys.
DEMO_ZONE_SCENARIO_KEYS: dict[str, str] = {
    "zone-a": ScenarioKey.NORMAL.value,
    "zone-b": ScenarioKey.NORMAL.value,
    "zone-c": ScenarioKey.PUMP_FAILURE.value,  # degraded / warning stress
    "zone-d": ScenarioKey.POWER_OUTAGE.value,  # critical stress
}
