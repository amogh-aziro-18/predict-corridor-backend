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

    @property
    def enabled_utility_kinds(self) -> list[str]:
        return [k.strip().lower() for k in self.enabled_utilities.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
