"""Планировщик периодического опроса сервисов и отправки алертов."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from balance_bot.models import AlertsConfig, ServiceConfig, ServiceStatus
from balance_bot.notifications import evaluate_alerts, format_alert_message
from balance_bot.plugins.base import ServicePlugin
from balance_bot.state import StateStore

if TYPE_CHECKING:
    from balance_bot.history import HistoryStore

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
        history: HistoryStore | None = None,
        *,
        poll_lock: asyncio.Lock | None = None,
        alerts_config: AlertsConfig | None = None,
        error_streaks: dict[str, int] | None = None,
    ) -> None:
        """Создаёт poller без запуска фоновой задачи.

        Args:
            service: Конфигурация сервиса.
            plugin: Экземпляр плагина для этого сервиса.
            state: Общее хранилище состояния.
            on_notify: Async-колбэк для push-уведомлений (HTML-текст).
            history: Опциональное хранилище истории баланса.
            poll_lock: Общий lock сервиса (исключает гонку фонового опроса и
                ``/refresh``).
            alerts_config: Параметры алертов; ``None`` — значения по умолчанию.
            error_streaks: Общий счётчик подряд неудачных опросов по сервисам.
        """
        self.service = service
        self.plugin = plugin
        self.state = state
        self.on_notify = on_notify
        self.history = history
        self._alerts_config = alerts_config or AlertsConfig()
        self._error_streaks = error_streaks if error_streaks is not None else {}
        self._poll_lock = poll_lock or asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def poll_once(self, *, suppress_alerts: bool = False) -> ServiceStatus:
        """Выполняет один опрос, обновляет state и шлёт новые алерты.

        Уведомление отправляется только для алертов, которых не было
        в предыдущем снимке (``risen = new - prev``), если не задан
        ``suppress_alerts``.

        Args:
            suppress_alerts: Обновить состояние без push-уведомлений.

        Returns:
            Актуальный ``ServiceStatus`` (в т.ч. с ``error``).
        """
        async with self._poll_lock:
            return await self._poll_once_unlocked(suppress_alerts=suppress_alerts)

    async def _poll_once_unlocked(self, *, suppress_alerts: bool = False) -> ServiceStatus:
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

        if self.history is not None:
            await self._persist_history(name, status)

        error_streak = self._update_error_streak(name, status)
        new_alerts = evaluate_alerts(
            self.service,
            status,
            error_streak=error_streak,
            error_confirm_failures=self._alerts_config.error_confirm_failures,
        )
        prev_alerts = self.state.get_active_alerts(name)
        risen = new_alerts - prev_alerts
        fallen = prev_alerts - new_alerts
        self.state.set_active_alerts(name, new_alerts)
        logger.debug(
            "Алерты '%s': prev=%s new=%s risen=%s fallen=%s streak=%s suppress=%s",
            name,
            sorted(prev_alerts),
            sorted(new_alerts),
            sorted(risen),
            sorted(fallen),
            error_streak,
            suppress_alerts,
        )

        if self._should_persist_alerts():
            await self._persist_alerts(name, new_alerts, error_streak)

        if not suppress_alerts:
            for alert in risen:
                message = format_alert_message(name, alert, status)
                logger.debug("Отправка нового алерта '%s' для '%s'", alert, name)
                await self.on_notify(message)

        return status

    def _update_error_streak(self, name: str, status: ServiceStatus) -> int:
        if status.error:
            streak = self._error_streaks.get(name, 0) + 1
        else:
            streak = 0
        self._error_streaks[name] = streak
        return streak

    def _should_persist_alerts(self) -> bool:
        return (
            self._alerts_config.persist
            and self.history is not None
        )

    async def _persist_alerts(
        self,
        name: str,
        alerts: set[str],
        error_streak: int,
    ) -> None:
        assert self.history is not None
        try:
            await self.history.save_alert_persistence(name, alerts, error_streak)
        except Exception as exc:
            logger.info(
                "Состояние алертов '%s': не удалось сохранить — %s",
                name,
                exc,
            )
            logger.debug(
                "Состояние алертов '%s': ошибка записи",
                name,
                exc_info=True,
            )

    async def _persist_history(self, name: str, status: ServiceStatus) -> None:
        """Записывает снимок в историю.

        Args:
            name: Имя сервиса.
            status: Снимок опроса.
        """
        assert self.history is not None
        try:
            await self.history.record(name, self.service.plugin, status)
        except Exception as exc:
            logger.info(
                "История баланса '%s': не удалось записать — %s",
                name,
                exc,
            )
            logger.debug(
                "История баланса '%s': ошибка записи",
                name,
                exc_info=True,
            )

    async def _loop(self, *, delay_first: bool) -> None:
        """Бесконечный цикл: ``poll_once`` → sleep(interval)."""
        interval = self.service.poll_interval_seconds
        if delay_first:
            logger.debug(
                "Цикл poller '%s': начальная задержка %ss",
                self.service.name,
                interval,
            )
            await asyncio.sleep(interval)
        while True:
            logger.debug("Цикл poller '%s': запуск poll_once()", self.service.name)
            await self.poll_once()
            logger.debug("Цикл poller '%s': sleep %ss", self.service.name, interval)
            await asyncio.sleep(interval)

    def start(self, *, delay_first: bool = False) -> None:
        """Запускает фоновую задачу ``_loop``.

        Args:
            delay_first: Если ``True``, первый опрос — после ``poll_interval_seconds``.
        """
        self._task = asyncio.create_task(
            self._loop(delay_first=delay_first),
            name=f"poller-{self.service.name}",
        )
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

    def __init__(
        self,
        state: StateStore,
        on_notify: NotifyCallback,
        history: HistoryStore | None = None,
        *,
        prune_interval_hours: int = 0,
        alerts_config: AlertsConfig | None = None,
    ) -> None:
        """Создаёт пустой планировщик.

        Args:
            state: Хранилище снимков и алертов.
            on_notify: Колбэк для уведомлений всем разрешённым пользователям.
            history: Опциональное хранилище истории баланса.
            prune_interval_hours: Интервал фонового ``prune``; ``0`` — не запускать.
            alerts_config: Параметры push-алертов.
        """
        self.state = state
        self.on_notify = on_notify
        self.history = history
        self._prune_interval_hours = prune_interval_hours
        self._alerts_config = alerts_config or AlertsConfig()
        self._error_streaks: dict[str, int] = {}
        self._pollers: list[ServicePoller] = []
        self._poll_locks: dict[str, asyncio.Lock] = {}
        self._prune_task: asyncio.Task | None = None

    def hydrate_error_streaks(self, streaks: dict[str, int]) -> None:
        """Восстанавливает счётчики ошибок из персистентного хранилища.

        Args:
            streaks: Имя сервиса → число подряд неудачных опросов.
        """
        self._error_streaks.update(streaks)

    def add_poller(self, service: ServiceConfig, plugin: ServicePlugin) -> None:
        """Регистрирует сервис для фонового опроса.

        Args:
            service: Конфигурация сервиса.
            plugin: Уже созданный экземпляр плагина.
        """
        poll_lock = self._poll_locks.setdefault(service.name, asyncio.Lock())
        self._pollers.append(
            ServicePoller(
                service,
                plugin,
                self.state,
                self.on_notify,
                history=self.history,
                poll_lock=poll_lock,
                alerts_config=self._alerts_config,
                error_streaks=self._error_streaks,
            )
        )
        logger.debug(
            "Scheduler: добавлен poller service=%s plugin=%s",
            service.name,
            service.plugin,
        )

    def start_all(self, *, delay_first: bool = False) -> None:
        """Запускает фоновые задачи всех poller'ов.

        Args:
            delay_first: Отложить первый опрос каждого poller'а на ``poll_interval``.
        """
        logger.debug(
            "Scheduler: start_all() count=%d delay_first=%s",
            len(self._pollers),
            delay_first,
        )
        for poller in self._pollers:
            poller.start(delay_first=delay_first)
        self._start_prune_loop()

    def _start_prune_loop(self) -> None:
        if self.history is None or self._prune_interval_hours <= 0:
            return
        if self._prune_task is not None:
            return
        self._prune_task = asyncio.create_task(
            self._prune_loop(),
            name="history-prune",
        )
        logger.debug(
            "Scheduler: фоновый prune каждые %s ч",
            self._prune_interval_hours,
        )

    async def _prune_loop(self) -> None:
        interval = self._prune_interval_hours * 3600
        while True:
            await asyncio.sleep(interval)
            await self._run_prune("плановый")

    async def _run_prune(self, reason: str) -> None:
        if self.history is None:
            return
        try:
            stats = await self.history.prune()
            if stats.deleted_rows > 0:
                logger.info(
                    "Scheduler: prune (%s) удалено %d строк, vacuum_pages=%s",
                    reason,
                    stats.deleted_rows,
                    stats.vacuum_pages,
                )
            else:
                logger.debug("Scheduler: prune (%s) нечего удалять", reason)
        except Exception as exc:
            logger.info("История баланса: prune (%s) не удался — %s", reason, exc)
            logger.debug("История баланса: prune", exc_info=True)

    async def stop_all(self) -> None:
        """Останавливает фоновый prune и все poller'ы."""
        if self._prune_task is not None:
            logger.debug("Scheduler: остановка фонового prune")
            self._prune_task.cancel()
            try:
                await self._prune_task
            except asyncio.CancelledError:
                pass
            self._prune_task = None
        logger.debug("Scheduler: stop_all() count=%d", len(self._pollers))
        await asyncio.gather(*(p.stop() for p in self._pollers))

    async def poll_all_now(self, *, suppress_alerts: bool = False) -> None:
        """Параллельно выполняет ``poll_once`` для каждого сервиса.

        Args:
            suppress_alerts: Не слать push-уведомления (состояние обновляется).
        """
        logger.debug(
            "Scheduler: poll_all_now() count=%d suppress_alerts=%s",
            len(self._pollers),
            suppress_alerts,
        )
        await asyncio.gather(
            *(p.poll_once(suppress_alerts=suppress_alerts) for p in self._pollers)
        )
        await self._run_prune("после poll_all_now")
