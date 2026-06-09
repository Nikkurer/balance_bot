"""Обработчик /refresh."""

import logging
from collections.abc import Awaitable, Callable

from aiogram import Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from balance_bot.handlers.status import send_status
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)


def register_refresh_handlers(
    dp: Dispatcher,
    state: StateStore,
    on_refresh: Callable[[], Awaitable[None]] | None,
) -> None:
    """Регистрирует команду ``/refresh``."""

    @dp.message(Command("refresh"))
    async def cmd_refresh(message: Message) -> None:
        logger.debug("Команда /refresh: chat_id=%s", message.chat.id)
        if on_refresh is None:
            await message.answer("Опрос недоступен.")
            return
        await message.answer("Опрос…")
        await on_refresh()
        await send_status(message, state)
