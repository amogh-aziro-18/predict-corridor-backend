"""Gas telemetry value generator — temporal random-walk with zone scenario drift."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from app.simulator.gas.scenarios import ZoneGasBehavior


class GasTelemetryGenerator:
    """
    Produces slowly evolving Gas metric values.

    Normal zones: mean-reverting walk around healthy baselines.
    Degrading zones: gradual drift (load/vibration/temp/leak up, pressure deviation)
    scaled by scenario intensity (0–1). State is keyed by ``asset_id:metric_key``.

    ``leak_indicator`` is a synthetic 0–1 POC signal — not certified CH₄.
    """

    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._state: dict[str, float] = {}
        self._zone_intensity: dict[str, float] = {}

    def zone_intensity(self, zone_id: str) -> float:
        return self._zone_intensity.get(zone_id, 0.0)

    def ramp_zone_intensity(self, zone_id: str, increment: float) -> float:
        if increment <= 0:
            self._zone_intensity.setdefault(zone_id, 0.0)
            return self._zone_intensity[zone_id]
        current = self._zone_intensity.get(zone_id, 0.28)
        next_i = min(1.0, current + increment)
        self._zone_intensity[zone_id] = next_i
        return next_i

    def next_value(
        self,
        metric_key: str,
        current: float | None = None,
        *,
        state_key: str | None = None,
        behavior: ZoneGasBehavior = ZoneGasBehavior.NORMAL,
        intensity: float = 0.0,
        baseline: float | None = None,
    ) -> float:
        key = state_key or metric_key
        healthy = baseline if baseline is not None else self._default(metric_key)

        base = self._state.get(key)
        if base is None:
            base = current if current is not None else healthy
            base = healthy + self._rng.uniform(-self._step(metric_key), self._step(metric_key))

        step = self._step(metric_key)
        pull_strength = 0.04 if behavior == ZoneGasBehavior.NORMAL else 0.015
        pull = (healthy - base) * pull_strength
        noise = self._rng.uniform(-step, step)

        drift = self._scenario_drift(metric_key, behavior=behavior, intensity=intensity)
        value = self._clamp(metric_key, base + noise + pull + drift)
        value = round(value, 3)
        self._state[key] = value
        return value

    def envelope(
        self,
        metric_key: str,
        *,
        unit: str,
        current: float | None = None,
        asset_id: str | None = None,
        behavior: ZoneGasBehavior = ZoneGasBehavior.NORMAL,
        intensity: float = 0.0,
        baseline: float | None = None,
    ) -> dict[str, Any]:
        state_key = f"{asset_id}:{metric_key}" if asset_id else metric_key
        value = self.next_value(
            metric_key,
            current,
            state_key=state_key,
            behavior=behavior,
            intensity=intensity,
            baseline=baseline,
        )
        return {
            "value": value,
            "unit": unit,
            "quality": "good",
            "metric_key": metric_key,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    def _scenario_drift(
        self,
        metric_key: str,
        *,
        behavior: ZoneGasBehavior,
        intensity: float,
    ) -> float:
        if behavior == ZoneGasBehavior.NORMAL or intensity <= 0:
            return 0.0
        strength = 1.0 if behavior == ZoneGasBehavior.DEGRADING else 1.75
        i = max(0.0, min(1.0, intensity)) * strength
        drifts = {
            "compressor_load_pct": 0.55 * i,
            "vibration_mm_s": 0.12 * i,
            "temperature_c": 0.40 * i,
            "pressure_bar": -0.08 * i,
            "flow_rate_nm3_h": -12.0 * i,
            "leak_indicator": 0.018 * i,
            "compressor_discharge_pressure_bar": -0.10 * i,
            "power_consumption_kw": 4.0 * i,
            "gas_quality_pct": -0.08 * i,
            "valve_position_pct": 0.25 * i,
        }
        return drifts.get(metric_key, 0.0)

    def _default(self, metric_key: str) -> float:
        return {
            "pressure_bar": 28.0,
            "flow_rate_nm3_h": 2000.0,
            "temperature_c": 40.0,
            "compressor_load_pct": 58.0,
            "vibration_mm_s": 2.0,
            "leak_indicator": 0.05,
            "gas_quality_pct": 96.0,
            "valve_position_pct": 55.0,
            "compressor_discharge_pressure_bar": 32.0,
            "power_consumption_kw": 380.0,
        }.get(metric_key, 1.0)

    def _step(self, metric_key: str) -> float:
        return {
            "pressure_bar": 0.08,
            "flow_rate_nm3_h": 18.0,
            "temperature_c": 0.35,
            "compressor_load_pct": 0.9,
            "vibration_mm_s": 0.05,
            "leak_indicator": 0.008,
            "gas_quality_pct": 0.05,
            "valve_position_pct": 0.4,
            "compressor_discharge_pressure_bar": 0.1,
            "power_consumption_kw": 3.0,
        }.get(metric_key, 0.2)

    def _clamp(self, metric_key: str, value: float) -> float:
        bounds = {
            "pressure_bar": (0.5, 45.0),
            "flow_rate_nm3_h": (50.0, 8000.0),
            "temperature_c": (5.0, 120.0),
            "compressor_load_pct": (5.0, 115.0),
            "vibration_mm_s": (0.2, 15.0),
            "leak_indicator": (0.0, 1.0),
            "gas_quality_pct": (80.0, 100.0),
            "valve_position_pct": (0.0, 100.0),
            "compressor_discharge_pressure_bar": (1.0, 50.0),
            "power_consumption_kw": (50.0, 800.0),
        }.get(metric_key, (None, None))
        low, high = bounds
        if low is not None:
            value = max(low, value)
        if high is not None:
            value = min(high, value)
        return value
