"""Планировщик периодического опроса сервисов и отправки алертов."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from balance_bot.models import ServiceConfig, ServiceStatus
from balance_bot.notifications import evaluate_alerts, format_alert_message
from balance_bot.plugins.base import ServicePlugin
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)

NotifyCallback = Callable[[str], Awaitable[None]]


def _log_poll_details(service_name: str, status: ServiceStatus) -> None:
    """Пишет в debug баланс и дату подписки после успешного опроса.

    Args:
        service_name: Имя сервиса.
        status: Успешный снимок без ``error``.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    parts = []
    if status.balance is not None:
        currency = status.currency or ""
        parts.append(f"баланс={status.balance:g} {currency}".strip())
    if status.subscription_end is not None:
        parts.append(f"подписка до={status.subscription_end.strftime('%Y-%m-%d')}")
    if parts:
        logger.debug("Опрос сервиса '%s': %s", service_name, ", ".join(parts))


class ServicePoller:
    """Периодический опрос одного сервиса через плагин."""

    def __init__(
        self,
        service: ServiceConfig,
        plugin: ServicePlugin,
        state: StateStore,
        on_notify: NotifyCallback,
    ) -> None:
        """Создаёт poller без запуска фоновой задачи.

        Args:
            service: Конфигурация сервиса.
            plugin: Экземпляр плагина для этого сервиса.
            state: Общее хранилище состояния.
            on_notify: Async-колбэк для push-уведомлений (HTML-текст).
        """
        self.service = service
        self.plugin = plugin
        self.state = state
        self.on_notify = on_notify
        self._task: asyncio.Task | None = None

    async def poll_once(self) -> ServiceStatus:
        """Выполняет один опрос, обновляет state и шлёт новые алерты.

        Уведомление отправляется только для алертов, которых не было
        в предыдущем снимке (``risen = new - prev``).

        Returns:
            Актуальный ``ServiceStatus`` (в т.ч. с ``error``).
        """
        name = self.service.name
        plugin_name = self.service.plugin
        logger.debug(
            "poll_once(): service=%s plugin=%s interval=%ss",
            name,
            plugin_name,
            self.service.poll_interval_seconds,
        )

        try:
            status = await self.plugin.fetch_status()
        except Exception as exc:
            logger.info(
                "Опрос сервиса '%s' (плагин %s): неуспешно",
                name,
                plugin_name,
            )
            logger.debug(
                "Опрос сервиса '%s': исключение — %s",
                name,
                exc,
                exc_info=True,
            )
            status = ServiceStatus(error=str(exc))
        else:
            if status.error:
                logger.info(
                    "Опрос сервиса '%s' (плагин %s): неуспешно",
                    name,
                    plugin_name,
                )
                logger.debug("Опрос сервиса '%s': %s", name, status.error)
            else:
                logger.info(
                    "Опрос сервиса '%s' (плагин %s): успешно",
                    name,
                    plugin_name,
                )
                _log_poll_details(name, status)

        self.state.set_status(name, status)

        new_alerts = evaluate_alerts(self.service, status)
        prev_alerts = self.state.get_active_alerts(name)
        risen = new_alerts - prev_alerts
        fallen = prev_alerts - new_alerts
        self.state.set_active_alerts(name, new_alerts)
        logger.debug(
            "Алерты '%s': prev=%s new=%s risen=%s fallen=%s",
            name,
            sorted(prev_alerts),
            sorted(new_alerts),
            sorted(risen),
            sorted(fallen),
        )

        for alert in risen:
            message = format_alert_message(name, alert, status)
            logger.debug("Отправка нового алерта '%s' для '%s'", alert, name)
            await self.on_notify(message)

        return status

    async def _loop(self) -> None:
        """Бесконечный цикл: ``poll_once`` → sleep(interval)."""
        interval = self.service.poll_interval_seconds
        while True:
            logger.debug("Цикл poller '%s': запуск poll_once()", self.service.name)
            await self.poll_once()
            logger.debug("Цикл poller '%s': sleep %ss", self.service.name, interval)
            await asyncio.sleep(interval)

    def start(self) -> None:
        """Запускает фоновую задачу ``_loop``."""
        self._task = asyncio.create_task(self._loop(), name=f"poller-{self.service.name}")
        logger.debug("Poller '%s' стартовал: task=%s", self.service.name, self._task.get_name())

    async def stop(self) -> None:
        """Отменяет фоновую задачу и закрывает плагин."""
        if self._task:
            logger.debug("Остановка poller '%s': cancel task", self.service.name)
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.debug("Остановка poller '%s': close plugin", self.service.name)
        await self.plugin.close()


class Scheduler:
    """Управляет несколькими ``ServicePoller`` и общим опросом по запросу."""

    def __init__(self, state: StateStore, on_notify: NotifyCallback) -> None:
        """Создаёт пустой планировщик.

        Args:
            state: Хранилище снимков и алертов.
            on_notify: Колбэк для уведомлений всем разрешённым пользователям.
        """
        self.state = state
        self.on_notify = on_notify
        self._pollers: list[ServicePoller] = []

    def add_poller(self, service: ServiceConfig, plugin: ServicePlugin) -> None:
        """Регистрирует сервис для фонового опроса.

        Args:
            service: Конфигурация сервиса.
            plugin: Уже созданный экземпляр плагина.
        """
        self._pollers.append(
            ServicePoller(service, plugin, self.state, self.on_notify)
        )
        logger.debug(
            "Scheduler: добавлен poller service=%s plugin=%s",
            service.name,
            service.plugin,
        )

    def start_all(self) -> None:
        """Запускает фоновые задачи всех poller'ов."""
        logger.debug("Scheduler: start_all() count=%d", len(self._pollers))
        for poller in self._pollers:
            poller.start()

    async def stop_all(self) -> None:
        """Останавливает все poller'ы параллельно."""
        logger.debug("Scheduler: stop_all() count=%d", len(self._pollers))
        await asyncio.gather(*(p.stop() for p in self._pollers))

    async def poll_all_now(self) -> None:
        """Параллельно выполняет ``poll_once`` для каждого сервиса."""
        logger.debug("Scheduler: poll_all_now() count=%d", len(self._pollers))
        await asyncio.gather(*(p.poll_once() for p in self._pollers))
