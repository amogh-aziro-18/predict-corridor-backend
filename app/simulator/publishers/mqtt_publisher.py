"""MQTT publisher for simulator traffic (optional)."""

from __future__ import annotations

import json
from typing import Any

import paho.mqtt.client as mqtt

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MqttPublisher:
    """Publishes JSON payloads to an MQTT broker. No-op connect until start()."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{settings.mqtt_client_id}-sim",
        )
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
        self._started = False

    def start(self) -> None:
        self._client.connect(self._settings.mqtt_host, self._settings.mqtt_port, keepalive=60)
        self._client.loop_start()
        self._started = True
        logger.info("Simulator MQTT publisher connected")

    def stop(self) -> None:
        if self._started:
            self._client.loop_stop()
            self._client.disconnect()
            self._started = False

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload)
        if not self._started:
            logger.debug("Dry publish %s %s", topic, body)
            return
        self._client.publish(topic, body, qos=0)
