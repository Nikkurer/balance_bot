"""Загрузка и разбор YAML-конфигурации приложения."""

from pathlib import Path

import yaml

from balance_bot.models import AppConfig, ServiceConfig
from balance_bot.validation import ConfigError, validate_config


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
        except KeyError as exc:
            raise ConfigError(
                f"services[{i}]: отсутствует обязательное поле {exc.args[0]!r}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"services[{i}]: неверное значение поля") from exc

    plugins_dir = raw.get("plugins_dir", "plugins")
    if not isinstance(plugins_dir, str):
        raise ConfigError("plugins_dir: должен быть строкой")

    config = AppConfig(
        bot_token=bot_token,
        allowed_user_ids=allowed_user_ids,
        services=services,
        plugins_dir=plugins_dir,
    )
    validate_config(config)
    return config
