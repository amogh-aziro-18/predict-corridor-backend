"""Scenario engine — reads persisted state and applies gradual metric modifiers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.domain.repositories.scenario_repository import ScenarioRepository
from app.scenarios.definitions import ScenarioKey
from app.scenarios.modifiers import apply_rule, rules_for
from app.scenarios.resolution import (
    DEMO_ZONE_SCENARIO_KEYS,
    normalize_zone_scenarios,
    resolve_effective_scenario,
)


class ScenarioEngine:
    """
    Applies operational scenario stress to simulator telemetry.

    Corridor-wide ``scenario_key`` / ``intensity`` remain the default.
    Optional ``zone_scenarios`` overrides apply only to matching zone assets.
    Intensity ramps from 0→1 after each selection so values evolve smoothly.
    """

    INTENSITY_STEP = 0.035
    MAX_INTENSITY = 1.0

    def __init__(self, db: Session) -> None:
        self._repo = ScenarioRepository(db)

    def current_key(self) -> ScenarioKey:
        state = self._repo.get_or_create()
        try:
            return ScenarioKey(state.scenario_key)
        except ValueError:
            return ScenarioKey.NORMAL

    def current_intensity(self) -> float:
        return float(self._repo.get_or_create().intensity)

    def zone_scenarios_map(self) -> dict[str, dict[str, Any]]:
        state = self._repo.get_or_create()
        return normalize_zone_scenarios(getattr(state, "zone_scenarios", None) or {})

    def effective_for_zone(self, zone_id: str | None) -> tuple[ScenarioKey, float]:
        state = self._repo.get_or_create()
        return resolve_effective_scenario(
            global_key=state.scenario_key,
            global_intensity=float(state.intensity),
            zone_scenarios=getattr(state, "zone_scenarios", None) or {},
            zone_id=zone_id,
        )

    def adjust_metric(
        self,
        *,
        metric_key: str,
        asset_type: str,
        value: float,
        zone_id: str | None = None,
        intensity: float | None = None,
    ) -> float:
        key, resolved_intensity = self.effective_for_zone(zone_id)
        if key == ScenarioKey.NORMAL:
            # Healthy operating envelope for normal zones (levels + pressure).
            return round(self._bound_normal_metric(metric_key, value), 2)

        intensity = resolved_intensity if intensity is None else intensity
        if intensity <= 0:
            return value

        rules = rules_for(key, asset_type)
        rule = rules.get(metric_key)
        if rule is None:
            return value

        adjusted = apply_rule(value, rule, intensity=intensity)
        # Keep stressed storage levels visually alive near the floor.
        if metric_key in {"tank_level", "reservoir_level"}:
            adjusted = self._near_floor_jitter(adjusted, floor=rule.floor)
        return round(adjusted, 2)

    @staticmethod
    def _bound_normal_metric(metric_key: str, value: float) -> float:
        """
        Soft bounds for normal_operations telemetry.

        Soft bounce near edges so values keep fluctuating instead of sticking
        to a hard clamp. Pressure stays in the ideal band so residual scenario
        stress does not keep firing critical pressure recommendations.
        """
        if metric_key == "tank_level":
            return ScenarioEngine._soft_band(value, low=74.0, high=89.0)
        if metric_key == "reservoir_level":
            return ScenarioEngine._soft_band(value, low=73.0, high=88.0)
        if metric_key == "pressure":
            # Above PRESSURE_LOW (1.8) / well clear of PRESSURE_CRITICAL_LOW (1.2).
            return ScenarioEngine._soft_band(value, low=2.45, high=4.05)
        if metric_key in {"pump_speed"}:
            return ScenarioEngine._soft_band(value, low=1100.0, high=1650.0)
        if metric_key == "pump_running":
            return 1.0 if value < 0.5 else min(1.0, max(0.0, value))
        if metric_key == "flow_rate":
            return ScenarioEngine._soft_band(value, low=120.0, high=900.0)
        return value

    @staticmethod
    def _soft_band(value: float, *, low: float, high: float) -> float:
        """Reflect slightly inward when outside the healthy band."""
        if value < low:
            return low + min(1.5, (low - value) * 0.35)
        if value > high:
            return high - min(1.5, (value - high) * 0.35)
        return value

    @staticmethod
    def _near_floor_jitter(value: float, *, floor: float | None) -> float:
        """Small noise so critical/degrading levels do not freeze at the floor."""
        import random

        if floor is None:
            return value + random.uniform(-0.35, 0.35)
        if value <= floor + 3.0:
            return floor + random.uniform(0.15, 2.4)
        return value + random.uniform(-0.45, 0.45)

    def ramp_intensity(self) -> float:
        """Increase global + per-zone intensities one simulator tick; returns global."""
        state = self._repo.get_or_create()
        changed = False

        if state.scenario_key == ScenarioKey.NORMAL.value:
            if state.intensity > 0:
                state.intensity = max(0.0, state.intensity - self.INTENSITY_STEP)
                changed = True
        else:
            new_intensity = min(self.MAX_INTENSITY, state.intensity + self.INTENSITY_STEP)
            if new_intensity != state.intensity:
                state.intensity = new_intensity
                changed = True

        zones = normalize_zone_scenarios(getattr(state, "zone_scenarios", None) or {})
        if zones:
            updated_zones: dict[str, dict[str, Any]] = {}
            for zone_id, entry in zones.items():
                key = entry["scenario_key"]
                intensity = float(entry["intensity"])
                if key == ScenarioKey.NORMAL.value:
                    new_i = max(0.0, intensity - self.INTENSITY_STEP) if intensity > 0 else 0.0
                else:
                    new_i = min(self.MAX_INTENSITY, intensity + self.INTENSITY_STEP)
                if new_i != intensity:
                    changed = True
                updated_zones[zone_id] = {"scenario_key": key, "intensity": new_i}
            state.zone_scenarios = updated_zones
            flag_modified(state, "zone_scenarios")

        if changed:
            self._repo.save(state)
        return float(state.intensity)

    def select_scenario(self, scenario_key: str) -> None:
        """Switch corridor-wide scenario, clear zone overrides, reset ramp."""
        parsed = ScenarioKey(scenario_key)
        state = self._repo.get_or_create()
        state.previous_scenario_key = state.scenario_key
        state.scenario_key = parsed.value
        state.intensity = 0.0 if parsed != ScenarioKey.NORMAL else 1.0
        state.zone_scenarios = {}
        flag_modified(state, "zone_scenarios")
        state.selected_at = datetime.now(timezone.utc)
        self._repo.save(state)

    def set_zone_scenarios(self, zone_keys: dict[str, str]) -> dict[str, dict[str, Any]]:
        """
        Configure per-zone scenario keys (existing ScenarioKey values).

        Intensity starts at 0 for non-normal zones so stress ramps in the simulator.
        Passing an empty dict clears overrides (global-only mode).
        """
        state = self._repo.get_or_create()
        if not zone_keys:
            state.zone_scenarios = {}
            flag_modified(state, "zone_scenarios")
            state.selected_at = datetime.now(timezone.utc)
            self._repo.save(state)
            return {}

        built: dict[str, dict[str, Any]] = {}
        for zone_id, scenario_key in zone_keys.items():
            parsed = ScenarioKey(scenario_key)
            # Non-normal zones start with a modest intensity so level drift is
            # visible within the first few simulator ticks (still ramps to 1.0).
            built[zone_id] = {
                "scenario_key": parsed.value,
                "intensity": 1.0 if parsed == ScenarioKey.NORMAL else 0.28,
            }
        state.zone_scenarios = built
        flag_modified(state, "zone_scenarios")
        # Keep corridor global on normal so unspecified zones stay calm when using
        # an asymmetric demo (A/B normal, C/D stressed).
        if all(e["scenario_key"] == ScenarioKey.NORMAL.value for e in built.values()):
            pass
        else:
            state.scenario_key = ScenarioKey.NORMAL.value
            state.intensity = 1.0
        state.selected_at = datetime.now(timezone.utc)
        self._repo.save(state)
        return normalize_zone_scenarios(state.zone_scenarios)

    def apply_demo_zone_asymmetric(self) -> dict[str, dict[str, Any]]:
        """Preset: Zone A/B normal, C pump_failure (degraded), D power_outage (critical)."""
        return self.set_zone_scenarios(dict(DEMO_ZONE_SCENARIO_KEYS))
