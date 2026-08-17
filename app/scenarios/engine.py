"""Scenario engine — reads persisted state and applies gradual metric modifiers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.domain.repositories.scenario_repository import ScenarioRepository
from app.scenarios.definitions import ScenarioKey
from app.scenarios.modifiers import apply_rule, rules_for


class ScenarioEngine:
    """
    Applies operational scenario stress to simulator telemetry.

    Intensity ramps from 0→1 after each scenario selection so values evolve
    smoothly instead of jumping.
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

    def adjust_metric(
        self,
        *,
        metric_key: str,
        asset_type: str,
        value: float,
        intensity: float | None = None,
    ) -> float:
        key = self.current_key()
        if key == ScenarioKey.NORMAL:
            return value

        intensity = self.current_intensity() if intensity is None else intensity
        if intensity <= 0:
            return value

        rules = rules_for(key, asset_type)
        rule = rules.get(metric_key)
        if rule is None:
            return value

        return round(apply_rule(value, rule, intensity=intensity), 2)

    def ramp_intensity(self) -> float:
        """Increase scenario intensity one simulator tick; returns new value."""
        state = self._repo.get_or_create()
        if state.scenario_key == ScenarioKey.NORMAL.value:
            if state.intensity > 0:
                state.intensity = max(0.0, state.intensity - self.INTENSITY_STEP)
                self._repo.save(state)
            return state.intensity

        new_intensity = min(self.MAX_INTENSITY, state.intensity + self.INTENSITY_STEP)
        if new_intensity != state.intensity:
            state.intensity = new_intensity
            self._repo.save(state)
        return state.intensity

    def select_scenario(self, scenario_key: str) -> None:
        """Switch active scenario and reset ramp (smooth transition)."""
        parsed = ScenarioKey(scenario_key)
        state = self._repo.get_or_create()
        state.previous_scenario_key = state.scenario_key
        state.scenario_key = parsed.value
        state.intensity = 0.0 if parsed != ScenarioKey.NORMAL else 1.0
        state.selected_at = datetime.now(timezone.utc)
        self._repo.save(state)
