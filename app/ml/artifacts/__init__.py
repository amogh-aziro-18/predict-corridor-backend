"""
Model artifact locations.

    artifacts/prophet_water.pkl          — corridor Prophet demand (Sprint 6B)
    artifacts/prophet_water_{zone}.pkl   — optional zone-scoped Prophet
    artifacts/pump_xgboost.pkl           — pump PdM (Sprint 6C)
"""

from __future__ import annotations

from pathlib import Path

ARTIFACT_DIR: Path = Path(__file__).resolve().parent

PROPHET_WATER_PATH: Path = ARTIFACT_DIR / "prophet_water.pkl"
PUMP_XGBOOST_PATH: Path = ARTIFACT_DIR / "pump_xgboost.pkl"

ARTIFACT_NAMES = {
    "prophet_water": PROPHET_WATER_PATH.name,
    "pump_xgboost": PUMP_XGBOOST_PATH.name,
}

__all__ = [
    "ARTIFACT_DIR",
    "ARTIFACT_NAMES",
    "PROPHET_WATER_PATH",
    "PUMP_XGBOOST_PATH",
]
