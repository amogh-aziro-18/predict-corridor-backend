"""Scenario selection and listing API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.scenario import CurrentScenarioOut, ScenarioOut, ScenarioSelectOut
from app.services.scenario_service import ScenarioService

router = APIRouter()


@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios(db: Session = Depends(db_dep)) -> list[ScenarioOut]:
    return ScenarioService(db).list_scenarios()


@router.get("/scenarios/current", response_model=CurrentScenarioOut)
def current_scenario(db: Session = Depends(db_dep)) -> CurrentScenarioOut:
    return ScenarioService(db).current()


@router.post("/scenarios/select/{scenario}", response_model=ScenarioSelectOut)
def select_scenario(scenario: str, db: Session = Depends(db_dep)) -> ScenarioSelectOut:
    return ScenarioService(db).select(scenario)
