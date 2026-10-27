"""
Operational corridor location — Phase 14.12 source of truth.

Demo / client context: Delhi, India industrial corridor.
Weather and environmental intelligence resolve to these coordinates
(not Bangalore, not a silent frontend label).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings

# Stable method id exposed on API location payloads.
LOCATION_METHOD_CONFIGURED_OPERATIONAL = "configured_operational_location"

# Well-known Central Delhi reference (India Gate vicinity) — Asia/Kolkata.
DEFAULT_CORRIDOR_CITY = "Delhi"
DEFAULT_CORRIDOR_COUNTRY = "India"
DEFAULT_CORRIDOR_DISPLAY_NAME = "Delhi, India"
DEFAULT_CORRIDOR_LATITUDE = 28.6139
DEFAULT_CORRIDOR_LONGITUDE = 77.2090
DEFAULT_CORRIDOR_TIMEZONE_HINT = "Asia/Kolkata"

# Zone anchors within the Delhi corridor (match seed placement origins).
# Used when zone_id is requested so zone assessments can differ spatially
# while remaining in the Delhi NCR operational context.
DELHI_ZONE_ANCHORS: dict[str, tuple[float, float]] = {
    "zone-a": (28.6120, 77.2090),
    "zone-b": (28.6350, 77.2200),
    "zone-c": (28.5900, 77.2500),
    "zone-d": (28.5700, 77.1800),
}


@dataclass(frozen=True)
class OperationalLocation:
    """Configured operational lookup point for weather / environment providers."""

    latitude: float
    longitude: float
    city: str
    country: str
    display_name: str
    timezone_hint: str
    scope: str
    zone_id: str | None
    method: str
    note: str


def get_operational_location(
    *,
    zone_id: str | None = None,
    settings: Settings | None = None,
) -> OperationalLocation:
    """
    Resolve the configured operational location for external providers.

    Corridor scope → Delhi config.
    Zone scope → Delhi zone anchor when known; otherwise corridor point.
    """
    cfg = settings or get_settings()
    city = (cfg.corridor_city or DEFAULT_CORRIDOR_CITY).strip() or DEFAULT_CORRIDOR_CITY
    country = (cfg.corridor_country or DEFAULT_CORRIDOR_COUNTRY).strip() or DEFAULT_CORRIDOR_COUNTRY
    display = (
        (cfg.corridor_display_name or "").strip()
        or f"{city}, {country}"
    )
    tz_hint = (
        (cfg.corridor_timezone_hint or DEFAULT_CORRIDOR_TIMEZONE_HINT).strip()
        or DEFAULT_CORRIDOR_TIMEZONE_HINT
    )
    corridor_lat = float(cfg.corridor_latitude)
    corridor_lon = float(cfg.corridor_longitude)

    if zone_id:
        anchor = DELHI_ZONE_ANCHORS.get(zone_id.strip().lower())
        if anchor is not None:
            lat, lon = anchor
            return OperationalLocation(
                latitude=round(lat, 6),
                longitude=round(lon, 6),
                city=city,
                country=country,
                display_name=f"{display} · {zone_id}",
                timezone_hint=tz_hint,
                scope="zone",
                zone_id=zone_id,
                method=LOCATION_METHOD_CONFIGURED_OPERATIONAL,
                note=(
                    f"Configured Delhi corridor zone anchor for {zone_id}. "
                    "Not a Bangalore location."
                ),
            )
        return OperationalLocation(
            latitude=round(corridor_lat, 6),
            longitude=round(corridor_lon, 6),
            city=city,
            country=country,
            display_name=f"{display} · {zone_id}",
            timezone_hint=tz_hint,
            scope="zone",
            zone_id=zone_id,
            method=LOCATION_METHOD_CONFIGURED_OPERATIONAL,
            note=(
                f"Unknown zone '{zone_id}' — using corridor Delhi coordinates. "
                "Not a Bangalore location."
            ),
        )

    return OperationalLocation(
        latitude=round(corridor_lat, 6),
        longitude=round(corridor_lon, 6),
        city=city,
        country=country,
        display_name=display,
        timezone_hint=tz_hint,
        scope="corridor",
        zone_id=None,
        method=LOCATION_METHOD_CONFIGURED_OPERATIONAL,
        note=(
            "Configured operational location for the Delhi, India industrial corridor. "
            "Weather and environmental intelligence query these coordinates."
        ),
    )
