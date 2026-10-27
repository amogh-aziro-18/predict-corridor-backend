"""
Centralized deterministic thresholds for Weather Impact Assessment (Phase 8C).

All signals are produced only when a measured value crosses an explicit threshold.
No probabilistic flood models or invented risk scores.

Operational interpretation (POC):
- Precipitation: hourly mm totals from Open-Meteo; advisory = light rain band.
- Wind speed: sustained 10 m wind converted to km/h; elevated affects outdoor ops.
- Wind gust: forecast gust peaks when present.
- Temperature: heat/cold stress bands for crew and demand planning.
- Relative humidity: elevated band for corrosion/comfort watch (informational).
- Pressure: low-pressure bands as storm-watch indicator (not a forecast model).
- Weather code: WMO adverse categories only (clear/overcast 0–3 produce no signal).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# --- Precipitation (mm per provider interval) ---
PRECIPITATION_ADVISORY_MM: Final[float] = 2.0
PRECIPITATION_ELEVATED_MM: Final[float] = 5.0
PRECIPITATION_SEVERE_MM: Final[float] = 10.0

# --- Wind speed (km/h, sustained) ---
WIND_SPEED_ADVISORY_KMH: Final[float] = 30.0
WIND_SPEED_ELEVATED_KMH: Final[float] = 50.0
WIND_SPEED_SEVERE_KMH: Final[float] = 70.0

# --- Wind gust (km/h, forecast when available) ---
WIND_GUST_ADVISORY_KMH: Final[float] = 45.0
WIND_GUST_ELEVATED_KMH: Final[float] = 65.0
WIND_GUST_SEVERE_KMH: Final[float] = 85.0

# --- Temperature (°C) ---
TEMPERATURE_LOW_C: Final[float] = 5.0
TEMPERATURE_HIGH_ADVISORY_C: Final[float] = 35.0
TEMPERATURE_HIGH_ELEVATED_C: Final[float] = 40.0

# --- Relative humidity (%) ---
HUMIDITY_ELEVATED_PCT: Final[float] = 85.0

# --- Surface pressure (hPa) — low bands only ---
PRESSURE_ADVISORY_HPA: Final[float] = 1000.0
PRESSURE_ELEVATED_HPA: Final[float] = 995.0
PRESSURE_SEVERE_HPA: Final[float] = 990.0

# WMO weather codes that warrant an adverse-condition signal (not 0–3 clear/overcast).
ADVERSARIAL_WEATHER_CODES: Final[dict[int, tuple[str, str]]] = {
    # code -> (severity, label)
    45: ("medium", "Fog"),
    48: ("medium", "Depositing rime fog"),
    51: ("low", "Light drizzle"),
    53: ("medium", "Moderate drizzle"),
    55: ("medium", "Dense drizzle"),
    56: ("medium", "Light freezing drizzle"),
    57: ("high", "Dense freezing drizzle"),
    61: ("low", "Slight rain"),
    63: ("medium", "Moderate rain"),
    65: ("high", "Heavy rain"),
    66: ("high", "Light freezing rain"),
    67: ("critical", "Heavy freezing rain"),
    71: ("medium", "Slight snow"),
    73: ("high", "Moderate snow"),
    75: ("critical", "Heavy snow"),
    77: ("low", "Snow grains"),
    80: ("medium", "Slight rain showers"),
    81: ("high", "Moderate rain showers"),
    82: ("critical", "Violent rain showers"),
    85: ("medium", "Slight snow showers"),
    86: ("high", "Heavy snow showers"),
    95: ("high", "Thunderstorm"),
    96: ("critical", "Thunderstorm with slight hail"),
    99: ("critical", "Thunderstorm with heavy hail"),
}

SEVERITY_RANK: Final[dict[str, int]] = {
    "none": 0,
    "info": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "critical": 5,
}


@dataclass(frozen=True)
class ThresholdBreaches:
    """Result of evaluating a numeric measurement against tiered thresholds."""

    severity: str
    threshold_rule: str
    explanation: str


def classify_precipitation(mm: float) -> ThresholdBreaches | None:
    if mm < PRECIPITATION_ADVISORY_MM:
        return None
    if mm >= PRECIPITATION_SEVERE_MM:
        return ThresholdBreaches(
            severity="critical",
            threshold_rule=f"precipitation_mm >= {PRECIPITATION_SEVERE_MM}",
            explanation=f"Precipitation {mm:.1f} mm meets severe rain threshold.",
        )
    if mm >= PRECIPITATION_ELEVATED_MM:
        return ThresholdBreaches(
            severity="high",
            threshold_rule=f"precipitation_mm >= {PRECIPITATION_ELEVATED_MM}",
            explanation=f"Precipitation {mm:.1f} mm meets elevated rain threshold.",
        )
    return ThresholdBreaches(
        severity="medium",
        threshold_rule=f"precipitation_mm >= {PRECIPITATION_ADVISORY_MM}",
        explanation=f"Precipitation {mm:.1f} mm meets advisory rain threshold.",
    )


def classify_wind_speed(kmh: float) -> ThresholdBreaches | None:
    if kmh < WIND_SPEED_ADVISORY_KMH:
        return None
    if kmh >= WIND_SPEED_SEVERE_KMH:
        return ThresholdBreaches(
            severity="critical",
            threshold_rule=f"wind_speed_kmh >= {WIND_SPEED_SEVERE_KMH}",
            explanation=f"Wind speed {kmh:.1f} km/h meets severe threshold.",
        )
    if kmh >= WIND_SPEED_ELEVATED_KMH:
        return ThresholdBreaches(
            severity="high",
            threshold_rule=f"wind_speed_kmh >= {WIND_SPEED_ELEVATED_KMH}",
            explanation=f"Wind speed {kmh:.1f} km/h meets elevated threshold.",
        )
    return ThresholdBreaches(
        severity="medium",
        threshold_rule=f"wind_speed_kmh >= {WIND_SPEED_ADVISORY_KMH}",
        explanation=f"Wind speed {kmh:.1f} km/h meets advisory threshold.",
    )


def classify_wind_gust(kmh: float) -> ThresholdBreaches | None:
    if kmh < WIND_GUST_ADVISORY_KMH:
        return None
    if kmh >= WIND_GUST_SEVERE_KMH:
        return ThresholdBreaches(
            severity="critical",
            threshold_rule=f"wind_gusts_kmh >= {WIND_GUST_SEVERE_KMH}",
            explanation=f"Wind gust {kmh:.1f} km/h meets severe threshold.",
        )
    if kmh >= WIND_GUST_ELEVATED_KMH:
        return ThresholdBreaches(
            severity="high",
            threshold_rule=f"wind_gusts_kmh >= {WIND_GUST_ELEVATED_KMH}",
            explanation=f"Wind gust {kmh:.1f} km/h meets elevated threshold.",
        )
    return ThresholdBreaches(
        severity="medium",
        threshold_rule=f"wind_gusts_kmh >= {WIND_GUST_ADVISORY_KMH}",
        explanation=f"Wind gust {kmh:.1f} km/h meets advisory threshold.",
    )


def classify_temperature(c: float) -> ThresholdBreaches | None:
    if c <= TEMPERATURE_LOW_C:
        return ThresholdBreaches(
            severity="medium",
            threshold_rule=f"temperature_c <= {TEMPERATURE_LOW_C}",
            explanation=f"Temperature {c:.1f} °C is below cold-stress advisory band.",
        )
    if c >= TEMPERATURE_HIGH_ELEVATED_C:
        return ThresholdBreaches(
            severity="high",
            threshold_rule=f"temperature_c >= {TEMPERATURE_HIGH_ELEVATED_C}",
            explanation=f"Temperature {c:.1f} °C meets elevated heat threshold.",
        )
    if c >= TEMPERATURE_HIGH_ADVISORY_C:
        return ThresholdBreaches(
            severity="medium",
            threshold_rule=f"temperature_c >= {TEMPERATURE_HIGH_ADVISORY_C}",
            explanation=f"Temperature {c:.1f} °C meets heat advisory threshold.",
        )
    return None


def classify_humidity(pct: float) -> ThresholdBreaches | None:
    if pct < HUMIDITY_ELEVATED_PCT:
        return None
    return ThresholdBreaches(
        severity="low",
        threshold_rule=f"relative_humidity_pct >= {HUMIDITY_ELEVATED_PCT}",
        explanation=f"Relative humidity {pct:.0f}% is in elevated band.",
    )


def classify_pressure(hpa: float) -> ThresholdBreaches | None:
    if hpa >= PRESSURE_ADVISORY_HPA:
        return None
    if hpa < PRESSURE_SEVERE_HPA:
        return ThresholdBreaches(
            severity="high",
            threshold_rule=f"pressure_hpa < {PRESSURE_SEVERE_HPA}",
            explanation=f"Surface pressure {hpa:.0f} hPa is in severe low-pressure band.",
        )
    if hpa < PRESSURE_ELEVATED_HPA:
        return ThresholdBreaches(
            severity="medium",
            threshold_rule=f"pressure_hpa < {PRESSURE_ELEVATED_HPA}",
            explanation=f"Surface pressure {hpa:.0f} hPa is in elevated low-pressure band.",
        )
    return ThresholdBreaches(
        severity="low",
        threshold_rule=f"pressure_hpa < {PRESSURE_ADVISORY_HPA}",
        explanation=f"Surface pressure {hpa:.0f} hPa is below advisory low-pressure band.",
    )


def classify_weather_code(code: int) -> ThresholdBreaches | None:
    entry = ADVERSARIAL_WEATHER_CODES.get(code)
    if entry is None:
        return None
    severity, label = entry
    return ThresholdBreaches(
        severity=severity,
        threshold_rule=f"weather_code in adverse WMO set ({code})",
        explanation=f"Provider weather code {code} ({label}) indicates adverse conditions.",
    )


def max_severity(severities: list[str]) -> str:
    if not severities:
        return "none"
    return max(severities, key=lambda s: SEVERITY_RANK.get(s, 0))
