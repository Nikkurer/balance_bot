from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ServiceStatus:
    """Снимок состояния сервиса, полученный от плагина."""

    balance: float | None = None
    currency: str | None = None
    subscription_end: datetime | None = None
    last_updated: datetime | None = None
    error: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class ServiceConfig:
    name: str
    plugin: str
    poll_interval_seconds: int
    balance_threshold: float | None = None
    subscription_warn_days: int | None = None
    plugin_config: dict = field(default_factory=dict)


@dataclass
class AppConfig:
    bot_token: str
    allowed_user_ids: list[int]
    services: list[ServiceConfig]
    plugins_dir: str = "plugins"
