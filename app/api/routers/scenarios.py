"""Scenario selection and listing API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.scenario import (
    CurrentScenarioOut,
    ScenarioOut,
    ScenarioSelectOut,
    ZoneScenarioConfigureIn,
    ZoneScenarioConfigureOut,
)
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


@router.put("/scenarios/zones", response_model=ZoneScenarioConfigureOut)
def configure_zone_scenarios(
    body: ZoneScenarioConfigureIn,
    db: Session = Depends(db_dep),
) -> ZoneScenarioConfigureOut:
    """
    Set or clear per-zone scenario overrides.

    Example body::

        {
          "zones": {
            "zone-a": "normal_operations",
            "zone-b": "normal_operations",
            "zone-c": "pump_failure",
            "zone-d": "power_outage"
          }
        }

    Empty ``zones`` clears overrides (corridor-wide mode only).
    """
    return ScenarioService(db).configure_zones(body.zones)


@router.post("/scenarios/zones/demo-asymmetric", response_model=ZoneScenarioConfigureOut)
def demo_asymmetric_zones(db: Session = Depends(db_dep)) -> ZoneScenarioConfigureOut:
    """Convenience: A/B normal, C degraded (pump_failure), D critical (power_outage)."""
    return ScenarioService(db).apply_demo_asymmetric()
