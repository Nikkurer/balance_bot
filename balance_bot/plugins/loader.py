import importlib
import logging
import sys
from pathlib import Path

from balance_bot.models import ServiceConfig
from balance_bot.plugins.base import ServicePlugin

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[ServicePlugin]] = {}


def resolve_plugins_dir(plugins_dir: Path, config_path: Path) -> Path:
    if plugins_dir.is_absolute():
        return plugins_dir
    return (config_path.parent / plugins_dir).resolve()


def discover_plugins(plugins_dir: Path) -> dict[str, type[ServicePlugin]]:
    """Загрузить все плагины из каталога (файлы *.py и пакеты с __init__.py)."""
    plugins_dir = plugins_dir.resolve()
    if not plugins_dir.is_dir():
        raise FileNotFoundError(f"Plugins directory not found: {plugins_dir}")

    plugins_path = str(plugins_dir)
    if plugins_path not in sys.path:
        sys.path.insert(0, plugins_path)

    registry: dict[str, type[ServicePlugin]] = {}

    for entry in sorted(plugins_dir.iterdir()):
        if entry.name.startswith(("_", ".")):
            continue

        module_name: str | None = None
        if entry.is_file() and entry.suffix == ".py" and entry.name != "__init__.py":
            module_name = entry.stem
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            module_name = entry.name
        else:
            continue

        try:
            module = importlib.import_module(module_name)
        except Exception:
            logger.exception("Failed to import plugin module %s", module_name)
            continue

        try:
            plugin_name, plugin_cls = _extract_plugin(module, default_name=module_name)
        except ValueError as exc:
            logger.error("Invalid plugin %s: %s", module_name, exc)
            continue

        if plugin_name in registry:
            raise ValueError(
                f"Duplicate plugin name '{plugin_name}' "
                f"({registry[plugin_name].__module__} vs {plugin_cls.__module__})"
            )

        registry[plugin_name] = plugin_cls
        logger.info("Loaded plugin '%s' from %s", plugin_name, module_name)

    return registry


def init_plugins(plugins_dir: Path) -> None:
    global _REGISTRY
    _REGISTRY = discover_plugins(plugins_dir)


def registered_plugins() -> list[str]:
    return sorted(_REGISTRY)


def create_plugin(service: ServiceConfig) -> ServicePlugin:
    plugin_cls = _REGISTRY.get(service.plugin)
    if plugin_cls is None:
        available = ", ".join(registered_plugins()) or "(none loaded)"
        raise ValueError(
            f"Unknown plugin '{service.plugin}' for service '{service.name}'. "
            f"Available: {available}"
        )
    return plugin_cls(service)


def _extract_plugin(module, default_name: str) -> tuple[str, type[ServicePlugin]]:
    plugin_name = getattr(module, "PLUGIN_NAME", default_name)

    plugin_cls = getattr(module, "Plugin", None)
    if plugin_cls is None:
        candidates = [
            obj
            for obj in module.__dict__.values()
            if isinstance(obj, type)
            and issubclass(obj, ServicePlugin)
            and obj is not ServicePlugin
        ]
        if len(candidates) == 1:
            plugin_cls = candidates[0]
        elif len(candidates) > 1:
            names = ", ".join(c.__name__ for c in candidates)
            raise ValueError(
                f"ambiguous plugin class ({names}); define class Plugin(ServicePlugin)"
            )
        else:
            raise ValueError("no ServicePlugin subclass found; define class Plugin")

    if not issubclass(plugin_cls, ServicePlugin):
        raise ValueError(f"{plugin_cls.__name__} must inherit ServicePlugin")

    return plugin_name, plugin_cls
