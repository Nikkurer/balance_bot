"""Обработчик /start."""

import logging

from aiogram import Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from balance_bot.bot_commands import register_bot_commands

logger = logging.getLogger(__name__)


def register_start_handlers(dp: Dispatcher) -> None:
    """Регистрирует команду ``/start``."""

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        logger.debug(
            "Команда /start: chat_id=%s user_id=%s",
            message.chat.id,
            message.from_user.id if message.from_user else None,
        )
        try:
            await register_bot_commands(message.bot, chat_id=message.chat.id)
        except Exception as exc:
            logger.warning("Не удалось обновить меню команд: %s", exc)

        await message.answer(
            "Мониторинг баланса и подписок.\n\n"
            "/status — статус\n"
            "/refresh — опрос\n"
            "/chart — график баланса"
        )
