"""Scenario selection and listing service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.scenario import (
    CurrentScenarioOut,
    ScenarioOut,
    ScenarioSelectOut,
    ZoneScenarioConfigureOut,
    ZoneScenarioStateOut,
)
from app.core.errors import NotFoundError, ValidationAppError
from app.domain.repositories.scenario_repository import ScenarioRepository
from app.scenarios.definitions import ScenarioKey, all_scenarios, get_scenario
from app.scenarios.engine import ScenarioEngine
from app.scenarios.resolution import DEMO_ZONE_SCENARIO_KEYS


class ScenarioService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = ScenarioRepository(db)
        self._engine = ScenarioEngine(db)

    def list_scenarios(self) -> list[ScenarioOut]:
        return [
            ScenarioOut(
                key=s.key.value,
                name=s.name,
                description=s.description,
                category=s.category,
                default_severity=s.default_severity,
            )
            for s in all_scenarios()
        ]

    def _zone_outs(self, raw: dict) -> list[ZoneScenarioStateOut]:
        outs: list[ZoneScenarioStateOut] = []
        for zone_id, entry in sorted(raw.items()):
            key = entry["scenario_key"]
            try:
                definition = get_scenario(key)
                name = definition.name
                severity = definition.default_severity
            except KeyError:
                name = key
                severity = None
            outs.append(
                ZoneScenarioStateOut(
                    zone_id=zone_id,
                    scenario_key=key,
                    intensity=float(entry["intensity"]),
                    name=name,
                    default_severity=severity,
                )
            )
        return outs

    def current(self) -> CurrentScenarioOut:
        state = self._repo.get_or_create()
        try:
            definition = get_scenario(state.scenario_key)
        except KeyError as exc:
            raise NotFoundError(f"Unknown scenario '{state.scenario_key}'") from exc

        zones = self._engine.zone_scenarios_map()
        return CurrentScenarioOut(
            key=definition.key.value,
            name=definition.name,
            description=definition.description,
            category=definition.category,
            default_severity=definition.default_severity,
            intensity=float(state.intensity),
            previous_key=state.previous_scenario_key,
            selected_at=state.selected_at,
            updated_at=state.updated_at,
            zone_scenarios=self._zone_outs(zones),
        )

    def select(self, scenario_key: str) -> ScenarioSelectOut:
        try:
            ScenarioKey(scenario_key)
        except ValueError as exc:
            raise ValidationAppError(
                f"Unsupported scenario '{scenario_key}'. "
                f"Use one of: {', '.join(k.value for k in ScenarioKey)}"
            ) from exc

        try:
            definition = get_scenario(scenario_key)
        except KeyError as exc:
            raise NotFoundError(f"Scenario '{scenario_key}' not found") from exc

        self._engine.select_scenario(scenario_key)
        state = self._repo.get_or_create()

        return ScenarioSelectOut(
            key=definition.key.value,
            name=definition.name,
            message=(
                f"Scenario '{definition.name}' selected corridor-wide. "
                "Per-zone overrides cleared. Telemetry will ramp gradually."
            ),
            intensity=float(state.intensity),
        )

    def configure_zones(self, zones: dict[str, str]) -> ZoneScenarioConfigureOut:
        validated: dict[str, str] = {}
        for zone_id, scenario_key in zones.items():
            if not zone_id or not str(zone_id).strip():
                raise ValidationAppError("zone_id must be a non-empty string")
            try:
                ScenarioKey(scenario_key)
            except ValueError as exc:
                raise ValidationAppError(
                    f"Unsupported scenario '{scenario_key}' for {zone_id}. "
                    f"Use one of: {', '.join(k.value for k in ScenarioKey)}"
                ) from exc
            validated[str(zone_id).strip()] = scenario_key

        raw = self._engine.set_zone_scenarios(validated)
        if not validated:
            message = "Per-zone overrides cleared. Corridor-wide scenario remains active."
        else:
            message = (
                "Per-zone scenarios configured. "
                "Simulator will ramp intensity independently per zone."
            )
        return ZoneScenarioConfigureOut(
            message=message,
            zone_scenarios=self._zone_outs(raw),
            raw=raw,
        )

    def apply_demo_asymmetric(self) -> ZoneScenarioConfigureOut:
        """Zone A/B normal, C pump_failure (degrading), D power_outage (critical)."""
        from app.scenarios.demo_water_levels import seed_asymmetric_demo_levels

        raw = self._engine.apply_demo_zone_asymmetric()
        seeded = seed_asymmetric_demo_levels(self._db)
        return ZoneScenarioConfigureOut(
            message=(
                "Demo asymmetric zones applied: "
                "A/B normal_operations (healthy level fluctuation), "
                "C pump_failure (degrading level drift), "
                "D power_outage (critical level drift). "
                f"Seeded {seeded} storage assets; simulator ramps intensity over time."
            ),
            zone_scenarios=self._zone_outs(raw),
            raw=raw,
        )

    @staticmethod
    def demo_zone_keys() -> dict[str, str]:
        return dict(DEMO_ZONE_SCENARIO_KEYS)
