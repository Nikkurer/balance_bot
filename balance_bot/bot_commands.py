"""Меню команд Telegram-бота."""

import logging

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonCommands,
)

logger = logging.getLogger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Справка по боту"),
    BotCommand(command="status", description="Состояние всех сервисов"),
    BotCommand(command="refresh", description="Принудительный опрос"),
    BotCommand(command="chart", description="График баланса"),
]


async def register_bot_commands(bot: Bot, *, chat_id: int | None = None) -> None:
    """Регистрирует команды бота в меню Telegram."""
    scopes: list[BotCommandScopeDefault | BotCommandScopeAllPrivateChats | BotCommandScopeChat] = [
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeDefault(),
    ]
    if chat_id is not None:
        scopes.insert(0, BotCommandScopeChat(chat_id=chat_id))
    logger.debug(
        "register_bot_commands(): chat_id=%s scopes=%d commands=%s",
        chat_id,
        len(scopes),
        [c.command for c in BOT_COMMANDS],
    )

    for scope in scopes:
        for language_code in (None, "ru"):
            logger.debug(
                "set_my_commands(): scope=%s language=%s",
                scope.__class__.__name__,
                language_code or "default",
            )
            await bot.set_my_commands(
                BOT_COMMANDS,
                scope=scope,
                language_code=language_code,
            )

    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.debug("set_chat_menu_button(): MenuButtonCommands")
