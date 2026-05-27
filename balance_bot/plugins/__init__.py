"""Загрузка плагинов из каталога и фабрика ``ServicePlugin``."""

from balance_bot.plugins.base import ServicePlugin
from balance_bot.plugins.loader import (
    create_plugin,
    discover_plugins,
    ensure_plugins_for_services,
    init_plugins,
)

__all__ = [
    "ServicePlugin",
    "create_plugin",
    "discover_plugins",
    "ensure_plugins_for_services",
    "init_plugins",
]
