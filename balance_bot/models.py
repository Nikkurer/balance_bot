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
class AlertsConfig:
    """Настройки push-алертов.

    Attributes:
        persist: Хранить активные алерты и счётчик ошибок в SQLite (нужен
            ``history.enabled``).
        suppress_on_startup: Не слать уведомления при первом ``poll_all_now``
            после старта (состояние всё равно обновляется).
        error_confirm_failures: Алерт ``error`` только после N неудачных опросов
            подряд (``1`` — сразу).
    """

    persist: bool = False
    suppress_on_startup: bool = True
    error_confirm_failures: int = 2


@dataclass
class HistoryConfig:
    """Настройки персистентной истории баланса (SQLite).

    Attributes:
        enabled: Запись истории после успешного опроса.
        path: Путь к файлу БД (относительно каталога конфига или абсолютный).
        retention_days: Удалять записи старше N дней; ``0`` — не применять.
        max_size_mb: Удалять старые записи, пока файл БД не ≤ N МБ; ``0`` — не применять.
        record_errors: Записывать сбои опроса в ``poll_errors``.
        chart_points_per_day: Макс. точек на графике за сутки; при большем числе
            записей в БД — усреднение по интервалам. ``0`` — все точки из БД.
        chart_max_points: Макс. точек баланса из БД для одного графика; при
            превышении берутся последние по времени. ``0`` — без лимита.
        prune_interval_hours: Периодический ``prune`` в фоне; ``0`` — только при
            старте и после ``poll_all_now``.
    """

    enabled: bool = False
    path: str = "data/balance_bot.db"
    retention_days: int = 0
    max_size_mb: int = 0
    record_errors: bool = False
    chart_points_per_day: int = 0
    chart_max_points: int = 10_000
    prune_interval_hours: int = 24


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
        alerts: Параметры push-уведомлений и их персистентности.
    """

    bot_token: str
    allowed_user_ids: list[int]
    services: list[ServiceConfig]
    plugins_dir: str = "plugins"
    timezone: str = "UTC"
    history: HistoryConfig = field(default_factory=HistoryConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
