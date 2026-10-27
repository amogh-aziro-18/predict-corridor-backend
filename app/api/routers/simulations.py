"""Isolated operational what-if simulation API.

Does NOT mutate live telemetry, simulator state, or persisted scenario selection.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.scenario import ScenarioOut
from app.api.schemas.simulation import (
    ParameterWhatIfIn,
    ParameterWhatIfOut,
    SimulationBaselineOut,
    SimulationParametersOut,
    WhatIfSimulationIn,
    WhatIfSimulationOut,
)
from app.services.operational_simulation_service import OperationalSimulationService
from app.services.scenario_service import ScenarioService

router = APIRouter()


@router.get("/simulations/scenarios", response_model=list[ScenarioOut])
def list_simulation_scenarios(db: Session = Depends(db_dep)) -> list[ScenarioOut]:
    """Catalog of scenarios usable for isolated what-if runs."""
    return ScenarioService(db).list_scenarios()


@router.get("/simulations/baseline/{asset_id}", response_model=SimulationBaselineOut)
def simulation_baseline(asset_id: str, db: Session = Depends(db_dep)) -> SimulationBaselineOut:
    """Live baseline snapshot for Digital Twin (read-only)."""
    payload = OperationalSimulationService(db).baseline_for_asset(asset_id)
    return SimulationBaselineOut.model_validate(payload)


@router.get("/simulations/parameters/{asset_id}", response_model=SimulationParametersOut)
def simulation_parameters(asset_id: str, db: Session = Depends(db_dep)) -> SimulationParametersOut:
    """Supported explainable parameter what-if options for an asset."""
    payload = OperationalSimulationService(db).parameters_for_asset(asset_id)
    return SimulationParametersOut.model_validate(payload)


@router.post("/simulations/what-if", response_model=WhatIfSimulationOut)
def run_what_if_simulation(
    body: WhatIfSimulationIn,
    db: Session = Depends(db_dep),
) -> WhatIfSimulationOut:
    """
    Run an isolated operational scenario projection.

    Applies deterministic scenario metric rules in-memory to a live telemetry
    snapshot. Does not write telemetry or change the live scenario engine state.
    """
    result = OperationalSimulationService(db).run_what_if(
        asset_id=body.asset_id,
        scenario_key=body.scenario_key,
        horizon_hours=body.horizon_hours,
    )
    return WhatIfSimulationOut.model_validate(result)


@router.post("/simulations/parameter-what-if", response_model=ParameterWhatIfOut)
def run_parameter_what_if(
    body: ParameterWhatIfIn,
    db: Session = Depends(db_dep),
) -> ParameterWhatIfOut:
    """
    Deterministic parameter what-if using documented Water/Power threshold bands.

    Prefer this for Digital Twin PoC. Returns unavailable honestly when the
    parameter has no live baseline or is unsupported.
    """
    result = OperationalSimulationService(db).run_parameter_what_if(
        asset_id=body.asset_id,
        parameter=body.parameter,
        delta_percent=body.delta_percent,
        absolute_value=body.absolute_value,
    )
    return ParameterWhatIfOut.model_validate(result)
