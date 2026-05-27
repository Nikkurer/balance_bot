"""Telegram-бот: команды, авторизация, рассылка уведомлений."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonCommands,
    Message,
    TelegramObject,
)

from balance_bot.models import AppConfig
from balance_bot.notifications import format_status_message
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Справка по боту"),
    BotCommand(command="status", description="Состояние всех сервисов"),
    BotCommand(command="refresh", description="Принудительный опрос"),
]


async def register_bot_commands(bot: Bot, *, chat_id: int | None = None) -> None:
    """Регистрирует команды бота в меню Telegram.

    Вызывает ``setMyCommands`` для нескольких scope и языков (в т.ч. ``ru``),
    затем устанавливает кнопку меню «Команды``.

    Args:
        bot: Экземпляр aiogram ``Bot``.
        chat_id: ID личного чата; если задан, команды также записываются в
            ``BotCommandScopeChat`` (наивысший приоритет в личке).
    """
    scopes: list[BotCommandScopeDefault | BotCommandScopeAllPrivateChats | BotCommandScopeChat] = [
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeDefault(),
    ]
    if chat_id is not None:
        scopes.insert(0, BotCommandScopeChat(chat_id=chat_id))

    for scope in scopes:
        for language_code in (None, "ru"):
            await bot.set_my_commands(
                BOT_COMMANDS,
                scope=scope,
                language_code=language_code,
            )

    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


class AuthMiddleware(BaseMiddleware):
    """Отклоняет сообщения от пользователей вне ``allowed_user_ids``."""

    def __init__(self, allowed_user_ids: set[int]) -> None:
        """Запоминает белый список ID.

        Args:
            allowed_user_ids: Разрешённые Telegram user ID.
        """
        self.allowed_user_ids = allowed_user_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Пропускает обработчик только для разрешённых пользователей.

        Args:
            handler: Следующий обработчик в цепочке.
            event: Входящее событие (сообщение и т.д.).
            data: Контекст aiogram.

        Returns:
            Результат ``handler`` или ``None``, если доступ запрещён.
        """
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
    """Создаёт ``Dispatcher`` с командами ``/start``, ``/status``, ``/refresh``.

    Args:
        config: Конфигурация (для списка разрешённых user ID).
        state: Хранилище снимков для ``/status``.
        on_refresh: Async-функция принудительного опроса всех сервисов;
            ``None`` — команда ``/refresh`` отвечает «недоступен».

    Returns:
        Настроенный экземпляр ``Dispatcher``.
    """
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware(set(config.allowed_user_ids)))

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        try:
            await register_bot_commands(message.bot, chat_id=message.chat.id)
        except Exception as exc:
            logger.warning("Не удалось обновить меню команд: %s", exc)

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
    """Отправляет HTML-сообщение каждому разрешённому пользователю.

    Ошибки отправки логируются, исключения не пробрасываются.

    Args:
        bot: Экземпляр ``Bot``.
        user_ids: Список Telegram chat/user ID.
        text: Текст уведомления (HTML).
    """
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
