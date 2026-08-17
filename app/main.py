"""FastAPI application factory and ASGI entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app import __version__
from app.api.middleware.errors import register_exception_handlers
from app.api.routers import build_api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.ingestion.mqtt.client import MqttClient
from app.ingestion.mqtt.handlers import TelemetryHandler
from app.ingestion.mqtt.topics import topic_config_from_settings
from app.ingestion.pipeline import IngestionPipeline
from app.utilities.registry import bootstrap_plugins

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("Starting %s v%s (%s)", settings.app_name, __version__, settings.app_env)

    bootstrap_plugins(settings.enabled_utility_kinds)
    logger.info("Enabled utility plugins: %s", settings.enabled_utility_kinds)

    app.state.mqtt_client = None
    app.state.ingestion_pipeline = IngestionPipeline(topic_config_from_settings(settings))
    if settings.mqtt_enabled:
        handler = TelemetryHandler(app.state.ingestion_pipeline)
        client = MqttClient(settings, on_message=handler)
        client.start()
        app.state.mqtt_client = client
        logger.info("MQTT ingestion started")
    else:
        logger.info("MQTT disabled (set MQTT_ENABLED=true to connect)")

    yield

    mqtt_client: MqttClient | None = getattr(app.state, "mqtt_client", None)
    if mqtt_client is not None:
        mqtt_client.stop()
        logger.info("MQTT client stopped")
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    # Allow the Vite frontend (and local tooling) to call the API.
    from fastapi.middleware.cors import CORSMiddleware

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(application)
    application.include_router(build_api_router(), prefix=settings.api_prefix)
    return application


app: FastAPI = create_app()


def get_app_state() -> dict[str, Any]:
    """Helper for tests / diagnostics."""
    return {"mqtt_client": getattr(app.state, "mqtt_client", None)}
