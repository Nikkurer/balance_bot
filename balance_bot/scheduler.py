import asyncio
import logging
from collections.abc import Awaitable, Callable

from balance_bot.models import ServiceConfig, ServiceStatus
from balance_bot.notifications import evaluate_alerts, format_alert_message
from balance_bot.plugins.base import ServicePlugin
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)

NotifyCallback = Callable[[str], Awaitable[None]]


class ServicePoller:
    """Периодический опрос одного сервиса через плагин."""

    def __init__(
        self,
        service: ServiceConfig,
        plugin: ServicePlugin,
        state: StateStore,
        on_notify: NotifyCallback,
    ) -> None:
        self.service = service
        self.plugin = plugin
        self.state = state
        self.on_notify = on_notify
        self._task: asyncio.Task | None = None

    async def poll_once(self) -> ServiceStatus:
        name = self.service.name
        try:
            status = await self.plugin.fetch_status()
        except Exception as exc:
            logger.exception("Failed to poll %s", name)
            status = ServiceStatus(error=str(exc))

        self.state.set_status(name, status)

        new_alerts = evaluate_alerts(self.service, status)
        prev_alerts = self.state.get_active_alerts(name)
        risen = new_alerts - prev_alerts
        self.state.set_active_alerts(name, new_alerts)

        for alert in risen:
            message = format_alert_message(name, alert, status)
            await self.on_notify(message)

        return status

    async def _loop(self) -> None:
        interval = self.service.poll_interval_seconds
        while True:
            await self.poll_once()
            await asyncio.sleep(interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name=f"poller-{self.service.name}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.plugin.close()


class Scheduler:
    def __init__(self, state: StateStore, on_notify: NotifyCallback) -> None:
        self.state = state
        self.on_notify = on_notify
        self._pollers: list[ServicePoller] = []

    def add_poller(self, service: ServiceConfig, plugin: ServicePlugin) -> None:
        self._pollers.append(
            ServicePoller(service, plugin, self.state, self.on_notify)
        )

    def start_all(self) -> None:
        for poller in self._pollers:
            poller.start()

    async def stop_all(self) -> None:
        await asyncio.gather(*(p.stop() for p in self._pollers))

    async def poll_all_now(self) -> None:
        await asyncio.gather(*(p.poll_once() for p in self._pollers))
