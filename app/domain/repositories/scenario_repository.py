"""Operational scenario state repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models.scenario_state import OperationalScenarioState
from app.scenarios.definitions import ScenarioKey


class ScenarioRepository:
    ROW_ID = "current"

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_or_create(self) -> OperationalScenarioState:
        row = self._db.get(OperationalScenarioState, self.ROW_ID)
        if row is None:
            row = OperationalScenarioState(
                id=self.ROW_ID,
                scenario_key=ScenarioKey.NORMAL.value,
                intensity=1.0,
                zone_scenarios={},
            )
            self._db.add(row)
            self._db.commit()
            self._db.refresh(row)
        return row

    def save(self, row: OperationalScenarioState) -> OperationalScenarioState:
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get(self) -> OperationalScenarioState | None:
        return self._db.get(OperationalScenarioState, self.ROW_ID)
