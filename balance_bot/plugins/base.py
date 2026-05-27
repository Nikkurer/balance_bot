"""Абстрактный контракт плагина мониторинга внешнего сервиса."""

from abc import ABC, abstractmethod

from balance_bot.models import ServiceConfig, ServiceStatus


class ServicePlugin(ABC):
    """Базовый класс плагина: один экземпляр на запись в ``services`` конфига.

    Attributes:
        service: Конфигурация сервиса с ``plugin_config``.
    """

    def __init__(self, service: ServiceConfig) -> None:
        """Сохраняет конфигурацию сервиса.

        Args:
            service: Запись из YAML ``services``.
        """
        self.service = service

    @abstractmethod
    async def fetch_status(self) -> ServiceStatus:
        """Запрашивает актуальное состояние у внешнего API.

        Returns:
            Снимок с балансом/подпиской или ``ServiceStatus(error=...)`` без
            выброса исключения (исключения обрабатывает ``ServicePoller``).
        """

    async def close(self) -> None:
        """Освобождает ресурсы (HTTP-сессии и т.п.).

        Вызывается при остановке poller'а. Переопределите при необходимости.
        """
