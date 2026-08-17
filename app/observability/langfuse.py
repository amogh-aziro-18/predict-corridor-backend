"""
Langfuse client wiring for the Sprint 7 agent layer.

Uses the installed Langfuse Python SDK v4 observation API
(``start_observation`` / ``update`` / ``end``). Tracing degrades gracefully
when keys are unset or initialisation fails.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Root observation for the current agent run; ended in flush_traces().
_active_root: Any | None = None


@lru_cache
def get_langfuse_client() -> Any | None:
    """Singleton Langfuse client, or ``None`` when unconfigured."""
    settings = get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        logger.info("Langfuse tracing enabled (host=%s)", settings.langfuse_host)
        return client
    except Exception:  # pragma: no cover — observability must never break the API
        logger.exception("Langfuse initialisation failed; tracing disabled")
        return None


def start_agent_trace(name: str, *, metadata: dict[str, Any] | None = None) -> Any | None:
    """Create a root observation for one agent-graph execution."""
    global _active_root

    client = get_langfuse_client()
    if client is None:
        _active_root = None
        return None
    try:
        # SDK v4: Langfuse.start_observation(...) replaces client.trace(...).
        # Default as_type="span" returns LangfuseSpan (must be ended with .end()).
        root = client.start_observation(
            name=name,
            as_type="span",
            metadata=metadata or {},
        )
        _active_root = root
        return root
    except Exception:  # pragma: no cover
        logger.exception("Failed to start Langfuse trace")
        _active_root = None
        return None


@contextmanager
def node_span(
    trace: Any | None,
    name: str,
    *,
    input_summary: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Span wrapper for one LangGraph node.

    Yields a mutable dict; node code may set ``output`` (JSON-safe summary)
    which is attached via ``update()`` before ``end()``.
    """
    holder: dict[str, Any] = {}
    if trace is None:
        yield holder
        return

    span = None
    try:
        # SDK v4: parent LangfuseSpan.start_observation(...) replaces trace.span(...).
        span = trace.start_observation(
            name=name,
            as_type="span",
            input=input_summary or {},
        )
    except Exception:  # pragma: no cover
        logger.exception("Failed to open Langfuse span %s", name)

    try:
        yield holder
    except Exception as exc:
        if span is not None:
            try:
                # end() only accepts end_time; set level/output via update() first.
                span.update(level="ERROR", status_message=str(exc))
                span.end()
            except Exception:  # pragma: no cover
                pass
        raise
    else:
        if span is not None:
            try:
                span.update(output=holder.get("output", {}))
                span.end()
            except Exception:  # pragma: no cover
                pass


def flush_traces() -> None:
    """End the root observation (if any) and flush buffered events."""
    global _active_root

    if _active_root is not None:
        try:
            _active_root.end()
        except Exception:  # pragma: no cover
            logger.exception("Failed to end Langfuse root observation")
        _active_root = None

    client = get_langfuse_client()
    if client is not None:
        try:
            client.flush()
        except Exception:  # pragma: no cover
            logger.exception("Langfuse flush failed")
