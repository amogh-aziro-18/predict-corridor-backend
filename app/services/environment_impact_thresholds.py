"""
Centralized deterministic thresholds for Environment Impact Assessment (Phase 9C).

POC operational thresholds for air-quality EnvironmentSignal observations.
These are NOT CPCB regulatory limits and NOT Indian National AQI bands.

AQI:
  Only classify when metadata.aqi_standard == "us_aqi".
  Bands follow the Open-Meteo / U.S. AQI scale description:
  0–50 good, 50–100 moderate, 100–150 unhealthy for sensitive groups,
  150–200 unhealthy, 200–300 very unhealthy, 300–500 hazardous.
  Source: https://open-meteo.com/en/docs/air-quality-api (U.S. AQI ranges).

Pollutant concentrations (μg/m³ from Open-Meteo CAMS):
  Conservative POC operational watch bands for outdoor/corridor operations.
  Instantaneous provider values — not 24h regulatory averaging.
  Documented as POC thresholds; not represented as CPCB compliance limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Reuse weather severity ranking vocabulary for consistency.
SEVERITY_RANK: Final[dict[str, int]] = {
    "none": 0,
    "info": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "critical": 5,
}

# --- US AQI (dimensionless index; only when aqi_standard == us_aqi) ---
# Boundaries aligned to Open-Meteo documented U.S. AQI ranges.
US_AQI_MODERATE: Final[float] = 50.0
US_AQI_SENSITIVE: Final[float] = 100.0
US_AQI_UNHEALTHY: Final[float] = 150.0
US_AQI_VERY_UNHEALTHY: Final[float] = 200.0
US_AQI_HAZARDOUS: Final[float] = 300.0

# --- Pollutant POC operational thresholds (μg/m³, instantaneous) ---
# Conservative operational watch bands — NOT CPCB regulatory standards.
PM25_ADVISORY: Final[float] = 35.0
PM25_ELEVATED: Final[float] = 55.0
PM25_SEVERE: Final[float] = 150.0

PM10_ADVISORY: Final[float] = 55.0
PM10_ELEVATED: Final[float] = 155.0
PM10_SEVERE: Final[float] = 255.0

NO2_ADVISORY: Final[float] = 100.0
NO2_ELEVATED: Final[float] = 200.0
NO2_SEVERE: Final[float] = 400.0

SO2_ADVISORY: Final[float] = 50.0
SO2_ELEVATED: Final[float] = 125.0
SO2_SEVERE: Final[float] = 350.0

CO_ADVISORY: Final[float] = 1000.0
CO_ELEVATED: Final[float] = 5000.0
CO_SEVERE: Final[float] = 10000.0

O3_ADVISORY: Final[float] = 100.0
O3_ELEVATED: Final[float] = 160.0
O3_SEVERE: Final[float] = 240.0

THRESHOLD_BASIS: Final[str] = (
    "POC operational thresholds. US AQI bands from Open-Meteo U.S. AQI documentation. "
    "Pollutant μg/m³ bands are conservative operational watch levels on instantaneous "
    "provider values — not CPCB regulatory limits and not Indian National AQI."
)

AQI_POLICY: Final[str] = (
    "Classify AQI only when metadata.aqi_standard == 'us_aqi'. "
    "Unknown or non-US AQI standards are not assessed with US thresholds. "
    "Never labeled as CPCB or Indian National AQI."
)


@dataclass(frozen=True)
class ThresholdBreaches:
    severity: str
    threshold_rule: str
    explanation: str


def max_severity(severities: list[str]) -> str:
    if not severities:
        return "none"
    return max(severities, key=lambda s: SEVERITY_RANK.get(s, 0))


def classify_us_aqi(value: float) -> ThresholdBreaches | None:
    """US AQI scale only — caller must verify aqi_standard == us_aqi."""
    if value < US_AQI_MODERATE:
        return None
    if value >= US_AQI_HAZARDOUS:
        return ThresholdBreaches(
            severity="critical",
            threshold_rule=f"us_aqi >= {US_AQI_HAZARDOUS}",
            explanation=(
                f"US AQI {value:.0f} is in the hazardous band "
                f"(≥{US_AQI_HAZARDOUS:.0f}). Not CPCB AQI."
            ),
        )
    if value >= US_AQI_VERY_UNHEALTHY:
        return ThresholdBreaches(
            severity="critical",
            threshold_rule=f"us_aqi >= {US_AQI_VERY_UNHEALTHY}",
            explanation=(
                f"US AQI {value:.0f} is in the very unhealthy band "
                f"(≥{US_AQI_VERY_UNHEALTHY:.0f}). Not CPCB AQI."
            ),
        )
    if value >= US_AQI_UNHEALTHY:
        return ThresholdBreaches(
            severity="high",
            threshold_rule=f"us_aqi >= {US_AQI_UNHEALTHY}",
            explanation=(
                f"US AQI {value:.0f} is in the unhealthy band "
                f"(≥{US_AQI_UNHEALTHY:.0f}). Not CPCB AQI."
            ),
        )
    if value >= US_AQI_SENSITIVE:
        return ThresholdBreaches(
            severity="medium",
            threshold_rule=f"us_aqi >= {US_AQI_SENSITIVE}",
            explanation=(
                f"US AQI {value:.0f} is unhealthy for sensitive groups "
                f"(≥{US_AQI_SENSITIVE:.0f}). Not CPCB AQI."
            ),
        )
    return ThresholdBreaches(
        severity="low",
        threshold_rule=f"us_aqi >= {US_AQI_MODERATE}",
        explanation=(
            f"US AQI {value:.0f} is in the moderate band "
            f"(≥{US_AQI_MODERATE:.0f}). Not CPCB AQI."
        ),
    )


def _tiered_ugm3(
    *,
    value: float,
    label: str,
    advisory: float,
    elevated: float,
    severe: float,
) -> ThresholdBreaches | None:
    if value < advisory:
        return None
    if value >= severe:
        return ThresholdBreaches(
            severity="critical",
            threshold_rule=f"{label}_ug_m3 >= {severe}",
            explanation=(
                f"{label} {value:.1f} μg/m³ meets severe POC operational threshold "
                f"(≥{severe:.0f}). Not a CPCB regulatory determination."
            ),
        )
    if value >= elevated:
        return ThresholdBreaches(
            severity="high",
            threshold_rule=f"{label}_ug_m3 >= {elevated}",
            explanation=(
                f"{label} {value:.1f} μg/m³ meets elevated POC operational threshold "
                f"(≥{elevated:.0f}). Not a CPCB regulatory determination."
            ),
        )
    return ThresholdBreaches(
        severity="medium",
        threshold_rule=f"{label}_ug_m3 >= {advisory}",
        explanation=(
            f"{label} {value:.1f} μg/m³ meets advisory POC operational threshold "
            f"(≥{advisory:.0f}). Not a CPCB regulatory determination."
        ),
    )


def classify_pm25(value: float) -> ThresholdBreaches | None:
    return _tiered_ugm3(
        value=value,
        label="pm25",
        advisory=PM25_ADVISORY,
        elevated=PM25_ELEVATED,
        severe=PM25_SEVERE,
    )


def classify_pm10(value: float) -> ThresholdBreaches | None:
    return _tiered_ugm3(
        value=value,
        label="pm10",
        advisory=PM10_ADVISORY,
        elevated=PM10_ELEVATED,
        severe=PM10_SEVERE,
    )


def classify_no2(value: float) -> ThresholdBreaches | None:
    return _tiered_ugm3(
        value=value,
        label="no2",
        advisory=NO2_ADVISORY,
        elevated=NO2_ELEVATED,
        severe=NO2_SEVERE,
    )


def classify_so2(value: float) -> ThresholdBreaches | None:
    return _tiered_ugm3(
        value=value,
        label="so2",
        advisory=SO2_ADVISORY,
        elevated=SO2_ELEVATED,
        severe=SO2_SEVERE,
    )


def classify_co(value: float) -> ThresholdBreaches | None:
    return _tiered_ugm3(
        value=value,
        label="co",
        advisory=CO_ADVISORY,
        elevated=CO_ELEVATED,
        severe=CO_SEVERE,
    )


def classify_o3(value: float) -> ThresholdBreaches | None:
    return _tiered_ugm3(
        value=value,
        label="o3",
        advisory=O3_ADVISORY,
        elevated=O3_ELEVATED,
        severe=O3_SEVERE,
    )
