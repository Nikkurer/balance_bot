from abc import ABC, abstractmethod

from balance_bot.models import ServiceConfig, ServiceStatus


class ServicePlugin(ABC):
    """Базовый контракт плагина сервиса."""

    def __init__(self, service: ServiceConfig) -> None:
        self.service = service

    @abstractmethod
    async def fetch_status(self) -> ServiceStatus:
        """Запросить актуальное состояние у внешнего сервиса."""

    async def close(self) -> None:
        """Освободить ресурсы (HTTP-сессии и т.п.)."""
