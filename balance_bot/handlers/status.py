"""Обработчик /status."""

import logging

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from balance_bot.notifications import format_status_message
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)


async def send_status(message: Message, state: StateStore) -> None:
    """Отправляет сводку статусов всех сервисов."""
    statuses = state.all_statuses()
    if not statuses:
        await message.answer("Нет данных. /refresh")
        return

    parts = [format_status_message(name, st) for name, st in sorted(statuses.items())]
    logger.debug("send_status: services=%d", len(parts))
    await message.answer("\n\n".join(parts), parse_mode=ParseMode.HTML)


def register_status_handlers(dp: Dispatcher, state: StateStore) -> None:
    """Регистрирует команду ``/status``."""

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        logger.debug("Команда /status: chat_id=%s", message.chat.id)
        await send_status(message, state)
