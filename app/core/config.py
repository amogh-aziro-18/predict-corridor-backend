"""Application settings loaded from environment / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the Predict Corridor API."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Predict Corridor API", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")
    api_prefix: str = Field(default="/api", alias="API_PREFIX")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/predict_corridor",
        alias="DATABASE_URL",
    )

    mqtt_enabled: bool = Field(default=False, alias="MQTT_ENABLED")
    mqtt_host: str = Field(default="localhost", alias="MQTT_HOST")
    mqtt_port: int = Field(default=1883, alias="MQTT_PORT")
    mqtt_username: str = Field(default="", alias="MQTT_USERNAME")
    mqtt_password: str = Field(default="", alias="MQTT_PASSWORD")
    mqtt_client_id: str = Field(default="predict-corridor-api", alias="MQTT_CLIENT_ID")
    mqtt_topic_prefix: str = Field(default="corridor", alias="MQTT_TOPIC_PREFIX")

    enabled_utilities: str = Field(default="water", alias="ENABLED_UTILITIES")

    # Langfuse tracing (Sprint 7 agent layer). Tracing is a no-op when unset.
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")

    # Optional LLM for agent summaries; deterministic template used when unset.
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    agent_llm_model: str = Field(default="gpt-4o-mini", alias="AGENT_LLM_MODEL")

    # External weather (Phase 8A — Open-Meteo keyless POC).
    weather_provider: str = Field(default="open_meteo", alias="WEATHER_PROVIDER")
    open_meteo_base_url: str = Field(
        default="https://api.open-meteo.com/v1/forecast",
        alias="OPEN_METEO_BASE_URL",
    )
    weather_cache_ttl_seconds: int = Field(default=600, alias="WEATHER_CACHE_TTL_SECONDS")
    weather_forecast_cache_ttl_seconds: int = Field(
        default=1800,
        alias="WEATHER_FORECAST_CACHE_TTL_SECONDS",
    )
    weather_http_timeout_seconds: float = Field(default=15.0, alias="WEATHER_HTTP_TIMEOUT_SECONDS")

    # Operational corridor location (Phase 14.12 — Delhi, India).
    # Weather + environment providers query these coordinates (not Bangalore).
    corridor_city: str = Field(default="Delhi", alias="CORRIDOR_CITY")
    corridor_country: str = Field(default="India", alias="CORRIDOR_COUNTRY")
    corridor_display_name: str = Field(
        default="Delhi, India",
        alias="CORRIDOR_DISPLAY_NAME",
    )
    corridor_latitude: float = Field(default=28.6139, alias="CORRIDOR_LATITUDE")
    corridor_longitude: float = Field(default=77.2090, alias="CORRIDOR_LONGITUDE")
    corridor_timezone_hint: str = Field(
        default="Asia/Kolkata",
        alias="CORRIDOR_TIMEZONE_HINT",
    )

    # External air quality / environment observations (Phase 9B — Open-Meteo keyless POC).
    # AQI standard remains provider US AQI unless a CPCB-capable provider is configured.
    # Never relabel US AQI as Indian/CPCB AQI.
    environment_provider: str = Field(
        default="open_meteo_air_quality",
        alias="ENVIRONMENT_PROVIDER",
    )
    open_meteo_air_quality_base_url: str = Field(
        default="https://air-quality-api.open-meteo.com/v1/air-quality",
        alias="OPEN_METEO_AIR_QUALITY_BASE_URL",
    )
    environment_cache_ttl_seconds: int = Field(
        default=600,
        alias="ENVIRONMENT_CACHE_TTL_SECONDS",
    )
    environment_http_timeout_seconds: float = Field(
        default=15.0,
        alias="ENVIRONMENT_HTTP_TIMEOUT_SECONDS",
    )

    @property
    def enabled_utility_kinds(self) -> list[str]:
        return [k.strip().lower() for k in self.enabled_utilities.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
