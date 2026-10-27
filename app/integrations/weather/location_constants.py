"""Stable identifiers for weather / environment location resolution metadata."""

from __future__ import annotations

# Arithmetic mean of lat/lon for in-scope assets with coordinates (legacy).
# Utility context is carried separately on WeatherLocation.utility_kind.
WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS = "centroid_of_assets"

# Configured operational corridor point (Phase 14.12 — Delhi, India).
WEATHER_LOCATION_METHOD_CONFIGURED_OPERATIONAL = "configured_operational_location"
