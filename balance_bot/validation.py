"""Семантическая валидация конфигурации после разбора YAML."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from balance_bot.models import AppConfig, ServiceConfig


class ConfigError(Exception):
    """Ошибка конфигурации с перечнем проблем в сообщении."""


_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
_PLACEHOLDER_TOKENS = frozenset(
    {
        "",
        "your_bot_token",
        "changeme",
        "replace_me",
        "bot_token",
        "token",
    }
)


def validate_config(config: AppConfig) -> None:
    """Проверяет обязательные поля и согласованность конфигурации.

    Args:
        config: Разобранный ``AppConfig``.

    Raises:
        ConfigError: Список проблем в тексте исключения (по одной строке на пункт).
    """
    errors: list[str] = []

    token = (config.bot_token or "").strip()
    if not token:
        errors.append("telegram.bot_token: обязателен (получите у @BotFather)")
    elif token.lower() in _PLACEHOLDER_TOKENS:
        errors.append(
            "telegram.bot_token: указан шаблон, замените на реальный токен от @BotFather"
        )
    elif not _TOKEN_RE.match(token):
        errors.append(
            "telegram.bot_token: неверный формат (ожидается <id>:<secret>, например 123456:ABC-DEF...)"
        )

    if not config.allowed_user_ids:
        errors.append(
            "telegram.allowed_user_ids: нужен хотя бы один Telegram user id"
        )
    else:
        for uid in config.allowed_user_ids:
            if uid <= 0:
                errors.append(
                    f"telegram.allowed_user_ids: недопустимый id {uid} (должен быть положительным)"
                )

    if not config.plugins_dir or not str(config.plugins_dir).strip():
        errors.append("plugins_dir: обязателен")
    try:
        ZoneInfo(config.timezone)
    except ZoneInfoNotFoundError:
        errors.append(
            "timezone: неизвестная IANA timezone (например, Europe/Moscow, UTC)"
        )

    if not config.services:
        errors.append("services: нужен хотя бы один сервис для мониторинга")
    else:
        seen_names: set[str] = set()
        for i, service in enumerate(config.services):
            prefix = f"services[{i}]"
            errors.extend(_validate_service(service, prefix, seen_names))

    errors.extend(_validate_history(config))

    if errors:
        raise ConfigError("\n".join(f"  - {e}" for e in errors))


def _validate_service(
    service: ServiceConfig, prefix: str, seen_names: set[str]
) -> list[str]:
    """Проверяет одну запись в ``services``.

    Args:
        service: Конфигурация сервиса.
        prefix: Префикс для сообщений (например, ``services[0]``).
        seen_names: Уже встреченные имена сервисов (мутируется при успехе).

    Returns:
        Список строк с описанием ошибок (пустой, если всё в порядке).
    """
    errors: list[str] = []

    name = (service.name or "").strip()
    if not name:
        errors.append(f"{prefix}.name: обязателен")
    elif name in seen_names:
        errors.append(f'{prefix}.name: дубликат "{name}"')
    else:
        seen_names.add(name)

    if not (service.plugin or "").strip():
        errors.append(f"{prefix}.plugin: обязателен")

    if service.poll_interval_seconds <= 0:
        errors.append(
            f"{prefix}.poll_interval_seconds: должен быть больше 0"
        )

    return errors


def _validate_history(config: AppConfig) -> list[str]:
    """Проверяет секцию ``history``.

    Args:
        config: Корневая конфигурация.

    Returns:
        Список ошибок.
    """
    history = config.history
    if not history.enabled:
        return []

    errors: list[str] = []
    if history.retention_days < 0:
        errors.append("history.retention_days: должен быть >= 0 (0 — не применять)")
    if history.max_size_mb < 0:
        errors.append("history.max_size_mb: должен быть >= 0 (0 — не применять)")
    if history.retention_days <= 0 and history.max_size_mb <= 0:
        errors.append(
            "history: при enabled задайте retention_days > 0 и/или max_size_mb > 0 "
            "(0 — не применять очистку по этому параметру)"
        )
    if history.chart_points_per_day < 0:
        errors.append(
            "history.chart_points_per_day: должен быть >= 0 (0 — все точки из БД)"
        )
    if history.prune_interval_hours < 0:
        errors.append(
            "history.prune_interval_hours: должен быть >= 0 (0 — без фонового prune)"
        )
    return errors
