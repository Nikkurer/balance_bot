"""Модели данных: снимок состояния сервиса и конфигурация приложения."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ServiceStatus:
    """Снимок состояния сервиса, полученный от плагина.

    Attributes:
        balance: Текущий баланс или ``None``, если плагин не вернул значение.
        currency: Код или символ валюты (например, ``RUB``).
        subscription_end: Дата окончания подписки в UTC (или с tzinfo).
        last_updated: Время последнего успешного опроса.
        error: Текст ошибки опроса; при непустом значении остальные поля могут
            отсутствовать.
        details: Дополнительные поля от плагина (не показываются пользователю).
    """

    balance: float | None = None
    currency: str | None = None
    subscription_end: datetime | None = None
    last_updated: datetime | None = None
    error: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class ServiceConfig:
    """Настройки мониторинга одного сервиса из YAML.

    Attributes:
        name: Уникальное имя сервиса в конфиге.
        plugin: Имя зарегистрированного плагина (``PLUGIN_NAME``).
        poll_interval_seconds: Интервал фонового опроса в секундах.
        balance_threshold: Порог низкого баланса; ``None`` — не проверять.
        subscription_warn_days: За сколько дней до ``subscription_end`` слать
            алерт; ``None`` — не проверять.
        plugin_config: Параметры, передаваемые в конструктор плагина.
    """

    name: str
    plugin: str
    poll_interval_seconds: int
    balance_threshold: float | None = None
    subscription_warn_days: int | None = None
    plugin_config: dict = field(default_factory=dict)


@dataclass
class HistoryConfig:
    """Настройки персистентной истории баланса (SQLite).

    Attributes:
        enabled: Запись истории после успешного опроса.
        path: Путь к файлу БД (относительно каталога конфига или абсолютный).
        retention_days: Удалять записи старше N дней; ``0`` — не применять.
        max_size_mb: Удалять старые записи, пока файл БД не ≤ N МБ; ``0`` — не применять.
        record_errors: Записывать строки с ``error`` (баланс ``None``).
    """

    enabled: bool = False
    path: str = "data/balance_bot.db"
    retention_days: int = 0
    max_size_mb: int = 0
    record_errors: bool = False


@dataclass
class AppConfig:
    """Корневая конфигурация бота после загрузки YAML.

    Attributes:
        bot_token: Токен Telegram Bot API.
        allowed_user_ids: Список Telegram user ID с доступом к боту.
        services: Сервисы для мониторинга.
        plugins_dir: Каталог с файлами плагинов (относительный или абсолютный).
        timezone: IANA timezone для сообщений и логов (например, ``Europe/Moscow``).
        history: Параметры SQLite-истории баланса.
    """

    bot_token: str
    allowed_user_ids: list[int]
    services: list[ServiceConfig]
    plugins_dir: str = "plugins"
    timezone: str = "UTC"
    history: HistoryConfig = field(default_factory=HistoryConfig)
