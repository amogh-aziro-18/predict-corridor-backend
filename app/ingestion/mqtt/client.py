"""MQTT client wrapper (HiveMQ-compatible)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from app.core.config import Settings
from app.core.logging import get_logger
from app.ingestion.mqtt.topics import TopicConfig, topic_config_from_settings

logger = get_logger(__name__)

MessageHandler = Callable[[str, bytes], None]


class MqttClient:
    """
    Thin wrapper around paho-mqtt.

    Connection is optional at startup (`MQTT_ENABLED`). Handlers are wired but
    Sprint 1 does not process business logic.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        on_message: MessageHandler | None = None,
    ) -> None:
        self._settings = settings
        self._topics: TopicConfig = topic_config_from_settings(settings)
        self._on_message = on_message
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
        )
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)

        self._client.on_connect = self._handle_connect
        self._client.on_message = self._handle_message
        self._connected = False

    @property
    def topics(self) -> TopicConfig:
        return self._topics

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        logger.info(
            "Connecting MQTT client to %s:%s",
            self._settings.mqtt_host,
            self._settings.mqtt_port,
        )
        self._client.connect_async(self._settings.mqtt_host, self._settings.mqtt_port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        logger.info("Stopping MQTT client")
        self._client.loop_stop()
        if self._connected:
            self._client.disconnect()
        self._connected = False

    def _handle_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        rc = getattr(reason_code, "value", reason_code)
        if rc != 0:
            logger.error("MQTT connect failed: %s", reason_code)
            self._connected = False
            return
        self._connected = True
        subscription = self._topics.telemetry_subscription()
        client.subscribe(subscription)
        logger.info("MQTT connected; subscribed to %s", subscription)

    def _handle_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        if self._on_message is None:
            return
        try:
            self._on_message(message.topic, message.payload)
        except Exception:
            logger.exception("MQTT message handler failed for topic=%s", message.topic)
