"""Health check endpoint."""

from fastapi import APIRouter, Request

from app.api.schemas.common import HealthResponse
from app.core.config import get_settings
from app.utilities.registry import registry

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = get_settings()
    mqtt_client = getattr(request.app.state, "mqtt_client", None)
    mqtt_connected = bool(mqtt_client and getattr(mqtt_client, "connected", False))
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        env=settings.app_env,
        utilities=registry.kinds(),
        mqtt_enabled=settings.mqtt_enabled,
        mqtt_connected=mqtt_connected,
    )
