"""Common response schemas."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    app: str
    env: str
    utilities: list[str] = Field(default_factory=list)
    mqtt_enabled: bool = False
    mqtt_connected: bool = False


class MessageResponse(BaseModel):
    message: str
