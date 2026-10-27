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
    dependencies,
    digital_twin,
    emergency,
    environment,
    forecasts,
    gis,
    health,
    impact,
    incidents,
    kpis,
    maintenance_predictions,
    operations,
    power_forecasts,
    power_maintenance_predictions,
    gas_forecasts,
    gas_maintenance_predictions,
    recommendations,
    reports,
    resilience,
    scenarios,
    settings,
    simulations,
    technicians,
    telemetry,
    utilities,
    weather,
    work_orders,
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
    api.include_router(
        power_maintenance_predictions.router, tags=["predictions", "ml", "power"]
    )
    api.include_router(
        gas_maintenance_predictions.router, tags=["predictions", "ml", "gas"]
    )
    api.include_router(gas_forecasts.router, tags=["predictions", "ml", "gas"])
    api.include_router(power_forecasts.router, tags=["predictions", "ml", "power"])
    api.include_router(dashboard.router, tags=["dashboard"])
    api.include_router(kpis.router, tags=["kpis"])
    api.include_router(ai.router, tags=["ai"])
    api.include_router(agents.router, tags=["agents"])
    api.include_router(scenarios.router, tags=["scenarios"])
    api.include_router(simulations.router, tags=["simulations"])
    api.include_router(dependencies.router, tags=["dependencies"])
    api.include_router(impact.router, tags=["impact"])
    api.include_router(recommendations.router, tags=["recommendations"])
    api.include_router(work_orders.router, tags=["work-orders"])
    api.include_router(alerts.router, tags=["alerts"])
    api.include_router(operations.router, tags=["operations"])
    api.include_router(technicians.router, tags=["technicians"])
    api.include_router(incidents.router, tags=["incidents"])
    api.include_router(emergency.router, tags=["emergency"])
    api.include_router(compliance.router, tags=["compliance"])
    api.include_router(resilience.router, tags=["resilience"])
    api.include_router(weather.router, tags=["weather"])
    api.include_router(environment.router, tags=["environment"])
    api.include_router(digital_twin.router, tags=["digital-twin"])
    api.include_router(reports.router, tags=["reports"])
    api.include_router(gis.router, tags=["gis"])
    api.include_router(settings.router, tags=["settings"])
    return api
