"""Загрузка и разбор YAML-конфигурации приложения."""

import logging
from pathlib import Path

import yaml

from balance_bot.models import AlertsConfig, AppConfig, HistoryConfig, ServiceConfig
from balance_bot.exceptions import ConfigError
from balance_bot.validation import validate_config

logger = logging.getLogger(__name__)


def load_config(path: str | Path) -> AppConfig:
    """Читает YAML-файл и возвращает проверенную конфигурацию.

    Args:
        path: Путь к ``config.yaml`` или аналогу.

    Returns:
        Объект ``AppConfig`` после ``validate_config``.

    Raises:
        ConfigError: Ошибка чтения файла, разбора YAML или валидации полей.
    """
    path = Path(path)
    logger.debug("load_config(): path=%s", path)
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError as exc:
        raise ConfigError(f"Не удалось прочитать конфиг {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Ошибка разбора YAML в {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Корень конфига {path} должен быть объектом (mapping)")

    telegram = raw.get("telegram")
    if not isinstance(telegram, dict):
        raise ConfigError("Секция telegram: обязательна")

    bot_token = telegram.get("bot_token")
    if bot_token is None:
        raise ConfigError("telegram.bot_token: обязателен")
    if not isinstance(bot_token, str):
        raise ConfigError("telegram.bot_token: должен быть строкой")

    allowed_raw = telegram.get("allowed_user_ids")
    if allowed_raw is None:
        raise ConfigError("telegram.allowed_user_ids: обязателен")
    if not isinstance(allowed_raw, list) or not allowed_raw:
        raise ConfigError("telegram.allowed_user_ids: нужен непустой список")

    try:
        allowed_user_ids = [int(uid) for uid in allowed_raw]
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "telegram.allowed_user_ids: все значения должны быть целыми числами"
        ) from exc

    services_raw = raw.get("services")
    if services_raw is None:
        raise ConfigError("services: обязателен")
    if not isinstance(services_raw, list):
        raise ConfigError("services: должен быть списком")

    services: list[ServiceConfig] = []
    for i, s in enumerate(services_raw):
        if not isinstance(s, dict):
            raise ConfigError(f"services[{i}]: должен быть объектом")
        try:
            services.append(
                ServiceConfig(
                    name=s["name"],
                    plugin=s["plugin"],
                    poll_interval_seconds=int(s["poll_interval_seconds"]),
                    balance_threshold=s.get("balance_threshold"),
                    subscription_warn_days=s.get("subscription_warn_days"),
                    plugin_config=s.get("plugin_config") or {},
                )
            )
            logger.debug(
                "load_config(): service[%d] name=%s plugin=%s interval=%s",
                i,
                s.get("name"),
                s.get("plugin"),
                s.get("poll_interval_seconds"),
            )
        except KeyError as exc:
            raise ConfigError(
                f"services[{i}]: отсутствует обязательное поле {exc.args[0]!r}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"services[{i}]: неверное значение поля") from exc

    plugins_dir = raw.get("plugins_dir", "plugins")
    if not isinstance(plugins_dir, str):
        raise ConfigError("plugins_dir: должен быть строкой")
    timezone_name = raw.get("timezone", "UTC")
    if not isinstance(timezone_name, str):
        raise ConfigError("timezone: должен быть строкой (IANA, например Europe/Moscow)")

    history = _parse_history(raw.get("history"))
    alerts = _parse_alerts(raw.get("alerts"), history_enabled=history.enabled)

    config = AppConfig(
        bot_token=bot_token,
        allowed_user_ids=allowed_user_ids,
        services=services,
        plugins_dir=plugins_dir,
        timezone=timezone_name,
        history=history,
        alerts=alerts,
    )
    validate_config(config)
    logger.debug(
        "load_config(): done telegram_users=%d services=%d timezone=%s plugins_dir=%s",
        len(config.allowed_user_ids),
        len(config.services),
        config.timezone,
        config.plugins_dir,
    )
    return config


def _parse_history(raw) -> HistoryConfig:
    """Разбирает секцию ``history`` из YAML.

    Args:
        raw: Значение ``history`` или ``None``.

    Returns:
        ``HistoryConfig`` (по умолчанию отключена).

    Raises:
        ConfigError: Неверный тип или значение полей.
    """
    if raw is None:
        return HistoryConfig()
    if not isinstance(raw, dict):
        raise ConfigError("history: должен быть объектом")

    enabled = bool(raw.get("enabled", False))
    path = raw.get("path", "data/balance_bot.db")
    if not isinstance(path, str) or not path.strip():
        raise ConfigError("history.path: должен быть непустой строкой")

    retention_days = _parse_history_int(raw, "retention_days", default=0)
    max_size_mb = _parse_history_int(raw, "max_size_mb", default=0)
    record_errors = bool(raw.get("record_errors", False))
    chart_points_per_day = _parse_history_int(raw, "chart_points_per_day", default=0)
    chart_max_points = _parse_history_int(raw, "chart_max_points", default=10_000)
    prune_interval_hours = _parse_history_int(raw, "prune_interval_hours", default=24)

    return HistoryConfig(
        enabled=enabled,
        path=path.strip(),
        retention_days=retention_days,
        max_size_mb=max_size_mb,
        record_errors=record_errors,
        chart_points_per_day=chart_points_per_day,
        chart_max_points=chart_max_points,
        prune_interval_hours=prune_interval_hours,
    )


def _parse_alerts(raw, *, history_enabled: bool) -> AlertsConfig:
    """Разбирает секцию ``alerts`` из YAML."""
    if raw is None:
        return AlertsConfig(persist=history_enabled)
    if not isinstance(raw, dict):
        raise ConfigError("alerts: должен быть объектом")

    persist = bool(raw.get("persist", history_enabled))
    suppress_on_startup = bool(raw.get("suppress_on_startup", True))
    error_confirm_failures = _parse_alerts_int(
        raw, "error_confirm_failures", default=2
    )

    return AlertsConfig(
        persist=persist,
        suppress_on_startup=suppress_on_startup,
        error_confirm_failures=error_confirm_failures,
    )


def _parse_alerts_int(raw: dict, key: str, *, default: int) -> int:
    """Парсит целочисленное поле секции alerts."""
    if key not in raw:
        return default
    value = raw[key]
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"alerts.{key}: должно быть целым числом") from exc


def _parse_history_int(raw: dict, key: str, *, default: int) -> int:
    """Парсит целочисленное поле секции history.

    Args:
        raw: Секция ``history``.
        key: Имя поля.
        default: Значение, если ключ отсутствует.

    Returns:
        Целое число.

    Raises:
        ConfigError: Неверный тип или значение.
    """
    if key not in raw:
        return default
    value = raw[key]
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"history.{key}: должно быть целым числом") from exc
