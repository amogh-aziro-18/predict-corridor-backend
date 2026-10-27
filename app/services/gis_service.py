"""
GIS operational context service (8E weather + 9E environment + 10B resilience).

Combines registered zones with WeatherImpactService, EnvironmentImpactService,
and ResilienceAssessmentService. Does not duplicate thresholds or fabricate
per-asset weather, pollution, or resilience values.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.gis import (
    GisEnvironmentContextOut,
    GisResilienceContextOut,
    GisWeatherContextOut,
    GisZoneEnvironmentContextOut,
    GisZoneResilienceContextOut,
    GisZoneWeatherContextOut,
)
from app.core.errors import EnvironmentUnavailableError, WeatherUnavailableError
from app.domain.repositories.zone_repository import ZoneRepository
from app.services.environment_impact_service import EnvironmentImpactService
from app.services.resilience_assessment_service import ResilienceAssessmentService
from app.services.weather_impact_service import WeatherImpactService

_SPATIAL_NOTE = (
    "Weather impact is evaluated at the configured Delhi, India operational "
    "location (corridor) and Delhi zone anchors. Individual asset markers use "
    "registry coordinates only; weather is not measured per asset. Static "
    "weather_exposure/flood_risk labels on assets are seed metadata, not live weather risk."
)

_ENVIRONMENT_SPATIAL_NOTE = (
    "Environmental impact (air quality) is evaluated at the configured Delhi, "
    "India operational location and Delhi zone anchors. Individual asset markers "
    "use registry coordinates only; AQI and pollutants are not measured per asset. "
    "Open-Meteo supplies US AQI (not CPCB / Indian National AQI). Static "
    "weather_exposure/flood_risk labels remain seed metadata."
)

_RESILIENCE_SPATIAL_NOTE = (
    "Resilience is assessed at corridor/zone scope using weather/environment "
    "drivers at the configured Delhi operational location, plus scenario state "
    "and static asset catalog posture — not a 0–100 score and not live flood probability."
)


class GisService:
    """Operational GIS context — weather + environment + resilience at corridor/zone scope."""

    def __init__(
        self,
        db: Session,
        *,
        impact_service: WeatherImpactService | None = None,
        environment_impact_service: EnvironmentImpactService | None = None,
        resilience_assessment_service: ResilienceAssessmentService | None = None,
    ) -> None:
        self._db = db
        self._impact = impact_service or WeatherImpactService(db)
        self._env_impact = environment_impact_service or EnvironmentImpactService(db)
        self._resilience = resilience_assessment_service or ResilienceAssessmentService(
            db,
            weather_impact_service=self._impact,
            environment_impact_service=self._env_impact,
        )
        self._zones = ZoneRepository(db)

    def get_weather_context(self, *, forecast_hours: int = 48) -> GisWeatherContextOut:
        """
        Corridor + per-zone weather impact for GIS panels, plus additive
        environment and resilience context.

        Raises WeatherUnavailableError when corridor weather assessment cannot be
        loaded. Zone weather / environment / resilience failures are reported
        honestly and do not fail the whole response (except corridor weather).
        """
        hours = max(1, min(forecast_hours, 168))
        corridor = self._impact.assess(zone_id=None, hours=hours)
        utility_kind = corridor.location.utility_kind

        zone_rows: list[GisZoneWeatherContextOut] = []
        for zone in self._zones.list():
            try:
                assessment = self._impact.assess(zone_id=zone.id, hours=hours)
                zone_rows.append(
                    GisZoneWeatherContextOut(
                        zone_id=zone.id,
                        zone_name=zone.name,
                        assessment=assessment,
                        unavailable=False,
                    )
                )
            except WeatherUnavailableError:
                zone_rows.append(
                    GisZoneWeatherContextOut(
                        zone_id=zone.id,
                        zone_name=zone.name,
                        assessment=None,
                        unavailable=True,
                    )
                )

        return GisWeatherContextOut(
            spatial_note=_SPATIAL_NOTE,
            utility_kind=utility_kind,
            corridor=corridor,
            zones=zone_rows,
            environment=self._environment_context(forecast_hours=hours),
            resilience=self._resilience_context(
                forecast_hours=hours,
                utility_kind=utility_kind,
            ),
        )

    def _environment_context(self, *, forecast_hours: int) -> GisEnvironmentContextOut:
        corridor = None
        corridor_unavailable = False
        try:
            corridor = self._env_impact.assess(zone_id=None, hours=forecast_hours)
        except EnvironmentUnavailableError:
            corridor_unavailable = True

        zone_rows: list[GisZoneEnvironmentContextOut] = []
        for zone in self._zones.list():
            try:
                assessment = self._env_impact.assess(zone_id=zone.id, hours=forecast_hours)
                zone_rows.append(
                    GisZoneEnvironmentContextOut(
                        zone_id=zone.id,
                        zone_name=zone.name,
                        assessment=assessment,
                        unavailable=False,
                    )
                )
            except EnvironmentUnavailableError:
                zone_rows.append(
                    GisZoneEnvironmentContextOut(
                        zone_id=zone.id,
                        zone_name=zone.name,
                        assessment=None,
                        unavailable=True,
                    )
                )

        return GisEnvironmentContextOut(
            spatial_note=_ENVIRONMENT_SPATIAL_NOTE,
            corridor=corridor,
            corridor_unavailable=corridor_unavailable,
            zones=zone_rows,
        )

    def _resilience_context(
        self,
        *,
        forecast_hours: int,
        utility_kind: str,
    ) -> GisResilienceContextOut:
        try:
            ctx = self._resilience.get_context(
                utility_kind=utility_kind,
                hours=forecast_hours,
            )
        except Exception:
            # Unexpected failure — do not fail weather GIS; report unavailable honestly.
            return GisResilienceContextOut(
                spatial_note=_RESILIENCE_SPATIAL_NOTE,
                corridor=None,
                corridor_unavailable=True,
                zones=[
                    GisZoneResilienceContextOut(
                        zone_id=zone.id,
                        zone_name=zone.name,
                        assessment=None,
                        unavailable=True,
                    )
                    for zone in self._zones.list()
                ],
            )

        by_zone = {row.zone_id: row for row in ctx.zones if row.zone_id}
        zone_rows: list[GisZoneResilienceContextOut] = []
        for zone in self._zones.list():
            assessment = by_zone.get(zone.id)
            if assessment is None:
                zone_rows.append(
                    GisZoneResilienceContextOut(
                        zone_id=zone.id,
                        zone_name=zone.name,
                        assessment=None,
                        unavailable=True,
                    )
                )
            else:
                zone_rows.append(
                    GisZoneResilienceContextOut(
                        zone_id=zone.id,
                        zone_name=zone.name,
                        assessment=assessment,
                        unavailable=False,
                    )
                )

        return GisResilienceContextOut(
            spatial_note=_RESILIENCE_SPATIAL_NOTE,
            corridor=ctx.corridor,
            corridor_unavailable=False,
            zones=zone_rows,
        )
