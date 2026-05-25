import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BotCommand, Message, TelegramObject

from balance_bot.models import AppConfig
from balance_bot.notifications import format_status_message
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Справка по боту"),
    BotCommand(command="status", description="Состояние всех сервисов"),
    BotCommand(command="refresh", description="Принудительный опрос"),
]


async def register_bot_commands(bot: Bot) -> None:
    """Регистрация команд в меню Telegram (setMyCommands)."""
    await bot.set_my_commands(BOT_COMMANDS)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_ids: set[int]) -> None:
        self.allowed_user_ids = allowed_user_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            if user_id not in self.allowed_user_ids:
                await event.answer("Доступ запрещён.")
                return None
        return await handler(event, data)


def create_dispatcher(
    config: AppConfig,
    state: StateStore,
    on_refresh: Callable[[], Awaitable[None]] | None = None,
) -> Dispatcher:
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware(set(config.allowed_user_ids)))

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        await message.answer(
            "Бот отслеживает баланс и срок подписки на подключённых сервисах.\n\n"
            "Команды:\n"
            "/status — состояние всех сервисов\n"
            "/refresh — принудительный опрос"
        )

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        statuses = state.all_statuses()
        if not statuses:
            await message.answer("Данных пока нет. Дождитесь первого опроса или /refresh.")
            return

        parts = [format_status_message(name, st) for name, st in sorted(statuses.items())]
        await message.answer("\n\n".join(parts), parse_mode=ParseMode.HTML)

    @dp.message(Command("refresh"))
    async def cmd_refresh(message: Message) -> None:
        if on_refresh is None:
            await message.answer("Опрос недоступен.")
            return
        await message.answer("Опрашиваю сервисы…")
        await on_refresh()
        await cmd_status(message)

    @dp.message(F.text)
    async def unknown(message: Message) -> None:
        await message.answer("Неизвестная команда. Используйте /status или /refresh.")

    return dp


async def notify_users(bot: Bot, user_ids: list[int], text: str) -> None:
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error(
                "Не удалось отправить уведомление пользователю %s: %s",
                uid,
                exc,
            )
            logger.debug("Детали отправки уведомления %s", uid, exc_info=True)
