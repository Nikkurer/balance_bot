"""Регистрация обработчиков команд Telegram-бота."""

from collections.abc import Awaitable, Callable

from aiogram import Dispatcher

from balance_bot.handlers.chart import register_chart_handlers
from balance_bot.handlers.refresh import register_refresh_handlers
from balance_bot.handlers.start import register_start_handlers
from balance_bot.handlers.status import register_status_handlers
from balance_bot.history import HistoryStore
from balance_bot.models import AppConfig
from balance_bot.state import StateStore


def register_handlers(
    dp: Dispatcher,
    *,
    config: AppConfig,
    state: StateStore,
    on_refresh: Callable[[], Awaitable[None]] | None,
    history: HistoryStore | None,
) -> None:
    """Подключает все обработчики команд к ``Dispatcher``."""
    register_start_handlers(dp)
    register_status_handlers(dp, state)
    register_refresh_handlers(dp, state, on_refresh)
    register_chart_handlers(dp, config=config, history=history)
