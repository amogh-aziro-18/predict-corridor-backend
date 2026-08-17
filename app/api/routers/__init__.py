"""API routers package."""

from fastapi import APIRouter

from app.api.routers import (
    agents,
    ai,
    alerts,
    assets,
    compliance,
    corridor,
    dashboard,
    digital_twin,
    environment,
    forecasts,
    gis,
    health,
    incidents,
    kpis,
    maintenance_predictions,
    operations,
    recommendations,
    reports,
    resilience,
    scenarios,
    settings,
    technicians,
    telemetry,
    utilities,
    weather,
    zones,
)


def build_api_router() -> APIRouter:
    """Compose all public API routers under the configured prefix."""
    api = APIRouter()
    api.include_router(health.router, tags=["health"])
    api.include_router(corridor.router, tags=["corridor"])
    api.include_router(zones.router, tags=["zones"])
    api.include_router(utilities.router, tags=["utilities"])
    api.include_router(assets.router, tags=["assets"])
    api.include_router(telemetry.router, tags=["telemetry"])
    api.include_router(forecasts.router, tags=["forecasts"])
    api.include_router(maintenance_predictions.router, tags=["predictions", "ml"])
    api.include_router(dashboard.router, tags=["dashboard"])
    api.include_router(kpis.router, tags=["kpis"])
    api.include_router(ai.router, tags=["ai"])
    api.include_router(agents.router, tags=["agents"])
    api.include_router(scenarios.router, tags=["scenarios"])
    api.include_router(recommendations.router, tags=["recommendations"])
    api.include_router(alerts.router, tags=["alerts"])
    api.include_router(operations.router, tags=["operations"])
    api.include_router(technicians.router, tags=["technicians"])
    api.include_router(incidents.router, tags=["incidents"])
    api.include_router(compliance.router, tags=["compliance"])
    api.include_router(resilience.router, tags=["resilience"])
    api.include_router(weather.router, tags=["weather"])
    api.include_router(environment.router, tags=["environment"])
    api.include_router(digital_twin.router, tags=["digital-twin"])
    api.include_router(reports.router, tags=["reports"])
    api.include_router(gis.router, tags=["gis"])
    api.include_router(settings.router, tags=["settings"])
    return api
