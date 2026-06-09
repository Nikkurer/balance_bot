"""Обнаружение и регистрация плагинов из каталога ``plugins/``."""

import importlib
import logging
import sys
from pathlib import Path

from balance_bot.exceptions import ConfigError
from balance_bot.models import ServiceConfig
from balance_bot.plugins.base import ServicePlugin

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[ServicePlugin]] = {}


def resolve_plugins_dir(plugins_dir: Path, config_path: Path) -> Path:
    """Разрешает относительный путь к каталогу плагинов.

    Args:
        plugins_dir: Путь из конфига (относительный или абсолютный).
        config_path: Путь к файлу конфигурации (база для относительного пути).

    Returns:
        Абсолютный путь к каталогу плагинов.
    """
    if plugins_dir.is_absolute():
        logger.debug("resolve_plugins_dir(): absolute=%s", plugins_dir)
        return plugins_dir
    resolved = (config_path.parent / plugins_dir).resolve()
    logger.debug(
        "resolve_plugins_dir(): relative=%s config=%s -> %s",
        plugins_dir,
        config_path,
        resolved,
    )
    return resolved


def discover_plugins(plugins_dir: Path) -> dict[str, type[ServicePlugin]]:
    """Загружает все плагины из каталога (файлы ``*.py`` и пакеты).

    Каталог добавляется в ``sys.path``. Модули с ошибками импорта пропускаются
    с записью в лог.

    Args:
        plugins_dir: Каталог с ``mock.py``, ``vdsina.py`` и т.д.

    Returns:
        Словарь ``PLUGIN_NAME`` → класс плагина.

    Raises:
        FileNotFoundError: Каталог не существует.
        ValueError: Дубликат ``PLUGIN_NAME`` у двух модулей.
    """
    plugins_dir = plugins_dir.resolve()
    logger.debug("discover_plugins(): scan dir=%s", plugins_dir)
    if not plugins_dir.is_dir():
        raise FileNotFoundError(f"Plugins directory not found: {plugins_dir}")

    plugins_path = str(plugins_dir)
    if plugins_path not in sys.path:
        sys.path.insert(0, plugins_path)

    registry: dict[str, type[ServicePlugin]] = {}

    for entry in sorted(plugins_dir.iterdir()):
        logger.debug("discover_plugins(): inspect entry=%s", entry.name)
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
            logger.debug("discover_plugins(): import module=%s", module_name)
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
    """Сканирует каталог и заполняет глобальный реестр плагинов.

    Args:
        plugins_dir: Каталог плагинов.
    """
    global _REGISTRY
    _REGISTRY = discover_plugins(plugins_dir)
    logger.debug("init_plugins(): registry=%s", sorted(_REGISTRY))


def registered_plugins() -> list[str]:
    """Возвращает отсортированный список имён загруженных плагинов.

    Returns:
        Имена для поля ``plugin`` в конфиге.
    """
    return sorted(_REGISTRY)


def ensure_plugins_for_services(services: list[ServiceConfig]) -> None:
    """Проверяет, что для каждого сервиса из конфига есть загруженный плагин.

    Args:
        services: Список сервисов из ``AppConfig``.

    Raises:
        ConfigError: Хотя бы один ``plugin`` не найден в реестре.
    """
    available = registered_plugins()
    errors: list[str] = []

    for service in services:
        if service.plugin in _REGISTRY:
            logger.info(
                "Сервис '%s': плагин '%s' найден",
                service.name,
                service.plugin,
            )
        else:
            logger.error(
                "Сервис '%s': плагин '%s' не найден (доступны: %s)",
                service.name,
                service.plugin,
                ", ".join(available) or "(нет загруженных плагинов)",
            )
            errors.append(
                f"сервис '{service.name}': плагин '{service.plugin}' не найден"
            )

    if errors:
        hint = ", ".join(available) or "(нет загруженных плагинов)"
        raise ConfigError(
            "Для части сервисов нет плагинов:\n"
            + "\n".join(f"  - {e}" for e in errors)
            + f"\n  Доступные плагины: {hint}"
        )


def create_plugin(service: ServiceConfig) -> ServicePlugin:
    """Создаёт экземпляр плагина для сервиса.

    Args:
        service: Конфигурация с полем ``plugin``.

    Returns:
        Новый экземпляр ``ServicePlugin``.

    Raises:
        ConfigError: Плагин не зарегистрирован.
    """
    plugin_cls = _REGISTRY.get(service.plugin)
    if plugin_cls is None:
        available = ", ".join(registered_plugins()) or "(none loaded)"
        raise ConfigError(
            f"сервис '{service.name}': плагин '{service.plugin}' не найден. "
            f"Доступные: {available}"
        )
    logger.debug(
        "create_plugin(): service=%s plugin=%s class=%s",
        service.name,
        service.plugin,
        plugin_cls.__name__,
    )
    return plugin_cls(service)


def _extract_plugin(module, default_name: str) -> tuple[str, type[ServicePlugin]]:
    """Извлекает имя и класс плагина из загруженного модуля.

    Ожидается ``PLUGIN_NAME`` и класс ``Plugin``, иначе единственный подкласс
    ``ServicePlugin``.

    Args:
        module: Импортированный модуль плагина.
        default_name: Имя файла/пакета, если ``PLUGIN_NAME`` не задан.

    Returns:
        Пара ``(plugin_name, plugin_cls)``.

    Raises:
        ValueError: Класс не найден или неоднозначен.
    """
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
