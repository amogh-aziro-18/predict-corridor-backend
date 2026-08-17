"""Scenario selection and listing service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.scenario import CurrentScenarioOut, ScenarioOut, ScenarioSelectOut
from app.core.errors import NotFoundError, ValidationAppError
from app.domain.repositories.scenario_repository import ScenarioRepository
from app.scenarios.definitions import ScenarioKey, all_scenarios, get_scenario
from app.scenarios.engine import ScenarioEngine


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

    def current(self) -> CurrentScenarioOut:
        state = self._repo.get_or_create()
        try:
            definition = get_scenario(state.scenario_key)
        except KeyError as exc:
            raise NotFoundError(f"Unknown scenario '{state.scenario_key}'") from exc

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
            message=f"Scenario '{definition.name}' selected. Telemetry will ramp gradually.",
            intensity=float(state.intensity),
        )
