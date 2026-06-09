"""Telegram-бот: диспетчер, авторизация, рассылка уведомлений."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.types import CallbackQuery, Message, TelegramObject

from balance_bot.bot_commands import register_bot_commands
from balance_bot.handlers import register_handlers
from balance_bot.history import HistoryStore
from balance_bot.models import AppConfig
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)

__all__ = ["AuthMiddleware", "create_dispatcher", "notify_users", "register_bot_commands"]


class AuthMiddleware(BaseMiddleware):
    """Отклоняет сообщения и callback от пользователей вне ``allowed_user_ids``."""

    def __init__(self, allowed_user_ids: set[int]) -> None:
        self.allowed_user_ids = allowed_user_ids

    def _user_id(self, event: TelegramObject) -> int | None:
        if isinstance(event, Message):
            return event.from_user.id if event.from_user else None
        if isinstance(event, CallbackQuery):
            return event.from_user.id if event.from_user else None
        return None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, (Message, CallbackQuery)):
            user_id = self._user_id(event)
            if user_id not in self.allowed_user_ids:
                logger.debug("AuthMiddleware: deny user_id=%s", user_id)
                if isinstance(event, Message):
                    await event.answer("Нет доступа.")
                else:
                    await event.answer("Нет доступа.", show_alert=True)
                return None
            logger.debug("AuthMiddleware: allow user_id=%s", user_id)
        return await handler(event, data)


def create_dispatcher(
    config: AppConfig,
    state: StateStore,
    on_refresh: Callable[[], Awaitable[None]] | None = None,
    history: HistoryStore | None = None,
) -> Dispatcher:
    """Создаёт ``Dispatcher`` с командами бота."""
    dp = Dispatcher()
    auth = AuthMiddleware(set(config.allowed_user_ids))
    dp.message.middleware(auth)
    dp.callback_query.middleware(auth)

    register_handlers(
        dp,
        config=config,
        state=state,
        on_refresh=on_refresh,
        history=history,
    )

    @dp.message(F.text)
    async def unknown(message: Message) -> None:
        logger.debug("Неизвестная команда/текст: chat_id=%s text=%r", message.chat.id, message.text)
        await message.answer("/status · /refresh · /chart")

    return dp


async def notify_users(bot: Bot, user_ids: list[int], text: str) -> None:
    """Отправляет HTML-сообщение каждому разрешённому пользователю."""
    from aiogram.enums import ParseMode

    for uid in user_ids:
        logger.debug("notify_users(): send uid=%s", uid)
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error(
                "Не удалось отправить уведомление пользователю %s: %s",
                uid,
                exc,
            )
            logger.debug("Детали отправки уведомления %s", uid, exc_info=True)
