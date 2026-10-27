"""
Model artifact locations.

    artifacts/prophet_water.pkl          — corridor Prophet demand (Sprint 6B)
    artifacts/prophet_water_{zone}.pkl   — optional zone-scoped Prophet
    artifacts/pump_xgboost.pkl           — pump PdM (Sprint 6C)
    artifacts/transformer_xgboost.pkl    — Power transformer PdM (Phase 14.3C1)
    artifacts/prophet_power_load.pkl     — Power load forecast (Phase 14.3C2)
    artifacts/gas_xgboost.pkl            — Gas PdM (Phase 15.3)
    artifacts/prophet_gas_demand.pkl     — Gas demand forecast (Phase 15.4)
"""

from __future__ import annotations

from pathlib import Path

ARTIFACT_DIR: Path = Path(__file__).resolve().parent

PROPHET_WATER_PATH: Path = ARTIFACT_DIR / "prophet_water.pkl"
PUMP_XGBOOST_PATH: Path = ARTIFACT_DIR / "pump_xgboost.pkl"
TRANSFORMER_XGBOOST_PATH: Path = ARTIFACT_DIR / "transformer_xgboost.pkl"
PROPHET_POWER_LOAD_PATH: Path = ARTIFACT_DIR / "prophet_power_load.pkl"
GAS_XGBOOST_PATH: Path = ARTIFACT_DIR / "gas_xgboost.pkl"
PROPHET_GAS_DEMAND_PATH: Path = ARTIFACT_DIR / "prophet_gas_demand.pkl"

ARTIFACT_NAMES = {
    "prophet_water": PROPHET_WATER_PATH.name,
    "pump_xgboost": PUMP_XGBOOST_PATH.name,
    "transformer_xgboost": TRANSFORMER_XGBOOST_PATH.name,
    "prophet_power_load": PROPHET_POWER_LOAD_PATH.name,
    "gas_xgboost": GAS_XGBOOST_PATH.name,
    "prophet_gas_demand": PROPHET_GAS_DEMAND_PATH.name,
}

__all__ = [
    "ARTIFACT_DIR",
    "ARTIFACT_NAMES",
    "PROPHET_WATER_PATH",
    "PUMP_XGBOOST_PATH",
    "TRANSFORMER_XGBOOST_PATH",
    "PROPHET_POWER_LOAD_PATH",
    "GAS_XGBOOST_PATH",
    "PROPHET_GAS_DEMAND_PATH",
]
