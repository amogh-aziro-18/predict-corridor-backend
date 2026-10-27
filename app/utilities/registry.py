"""Utility registry — discover and resolve plugins by kind."""

from __future__ import annotations

from app.core.errors import PluginError
from app.core.logging import get_logger
from app.utilities.base import UtilityPlugin

logger = get_logger(__name__)


class UtilityRegistry:
    """In-process registry of enabled utility plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, UtilityPlugin] = {}

    def register(self, plugin: UtilityPlugin) -> None:
        kind = plugin.kind.lower()
        if kind in self._plugins:
            raise PluginError(f"Utility plugin already registered: {kind}")
        self._plugins[kind] = plugin
        logger.info("Registered utility plugin: %s (%s)", kind, plugin.display_name)

    def get(self, kind: str) -> UtilityPlugin:
        plugin = self._plugins.get(kind.lower())
        if plugin is None:
            raise PluginError(f"Unknown or disabled utility plugin: {kind}")
        return plugin

    def list(self) -> list[UtilityPlugin]:
        return list(self._plugins.values())

    def kinds(self) -> list[str]:
        return sorted(self._plugins.keys())


# Process-wide registry instance populated at application startup.
registry = UtilityRegistry()


def bootstrap_plugins(enabled_kinds: list[str]) -> UtilityRegistry:
    """
    Register plugins listed in configuration.

    Water, Power, Gas, and Communications are implemented.
    Other kinds are ignored with a warning.
    """
    from app.utilities.communications.plugin import CommunicationsPlugin
    from app.utilities.gas.plugin import GasPlugin
    from app.utilities.power.plugin import PowerPlugin
    from app.utilities.water.plugin import WaterPlugin

    available: dict[str, type[UtilityPlugin]] = {
        "water": WaterPlugin,
        "power": PowerPlugin,
        "gas": GasPlugin,
        "communications": CommunicationsPlugin,
    }

    for kind in enabled_kinds:
        key = kind.lower()
        plugin_cls = available.get(key)
        if plugin_cls is None:
            logger.warning("No plugin implementation for utility kind '%s' — skipped", key)
            continue
        if key not in registry.kinds():
            registry.register(plugin_cls())

    return registry
