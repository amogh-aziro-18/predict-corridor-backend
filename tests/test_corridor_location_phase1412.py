"""Phase 14.12 — Delhi operational location for weather / environment."""

from __future__ import annotations

from app.integrations.corridor_location import (
    DEFAULT_CORRIDOR_LATITUDE,
    DEFAULT_CORRIDOR_LONGITUDE,
    get_operational_location,
)
from app.integrations.environment.location import resolve_environment_location
from app.integrations.weather.location import resolve_weather_location
from app.db.session import SessionLocal


def test_operational_location_is_delhi_not_bangalore() -> None:
    op = get_operational_location()
    assert op.city == "Delhi"
    assert op.country == "India"
    assert op.display_name == "Delhi, India"
    assert op.latitude == DEFAULT_CORRIDOR_LATITUDE
    assert op.longitude == DEFAULT_CORRIDOR_LONGITUDE
    # Bangalore is ~12.97N — must not match.
    assert op.latitude > 20.0
    assert "Bangalore" not in (op.note or "")
    assert "Delhi" in op.note


def test_zone_anchors_remain_in_delhi_band() -> None:
    corridor = get_operational_location()
    zone_c = get_operational_location(zone_id="zone-c")
    assert zone_c.scope == "zone"
    assert zone_c.zone_id == "zone-c"
    assert 28.4 <= zone_c.latitude <= 28.8
    assert 77.1 <= zone_c.longitude <= 77.4
    assert (zone_c.latitude, zone_c.longitude) != (corridor.latitude, corridor.longitude)


def test_weather_and_environment_resolvers_agree_on_delhi() -> None:
    db = SessionLocal()
    try:
        weather = resolve_weather_location(db)
        environment = resolve_environment_location(db, utility_kind="water")
    finally:
        db.close()

    assert weather.latitude == environment.latitude == 28.6139
    assert weather.longitude == environment.longitude == 77.2090
    assert weather.method == environment.method == "configured_operational_location"
    assert weather.display_name == "Delhi, India"
    assert environment.display_name == "Delhi, India"
