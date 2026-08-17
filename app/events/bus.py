"""In-process domain event bus — Sprint 1 stub."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[Any], None]


class EventBus:
    """Simple synchronous pub/sub for decoupling ingestion from consumers."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event_type: str, payload: Any) -> None:
        for handler in self._handlers.get(event_type, []):
            try:
                handler(payload)
            except Exception:
                logger.exception("Event handler failed for %s", event_type)


event_bus = EventBus()
