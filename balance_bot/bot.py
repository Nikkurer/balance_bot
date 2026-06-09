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
    BufferedInputFile,
    CallbackQuery,
    MenuButtonCommands,
    Message,
    TelegramObject,
)

from balance_bot.charts import (
    CHART_PERIODS,
    parse_chart_command_args,
    parse_period_callback,
    parse_service_callback,
    period_keyboard,
    render_balance_chart,
    resolve_service_name,
    service_keyboard,
)
from balance_bot.history import HistoryStore
from balance_bot.models import AppConfig
from balance_bot.notifications import escape_html, format_status_message
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Справка по боту"),
    BotCommand(command="status", description="Состояние всех сервисов"),
    BotCommand(command="refresh", description="Принудительный опрос"),
    BotCommand(command="chart", description="График баланса"),
]


async def register_bot_commands(bot: Bot, *, chat_id: int | None = None) -> None:
    """Регистрирует команды бота в меню Telegram.

    Вызывает ``setMyCommands`` для нескольких scope и языков (в т.ч. ``ru``),
    затем устанавливает кнопку меню «Команды».

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


class AuthMiddleware(BaseMiddleware):
    """Отклоняет сообщения и callback от пользователей вне ``allowed_user_ids``."""

    def __init__(self, allowed_user_ids: set[int]) -> None:
        """Запоминает белый список ID.

        Args:
            allowed_user_ids: Разрешённые Telegram user ID.
        """
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
        """Пропускает обработчик только для разрешённых пользователей.

        Args:
            handler: Следующий обработчик в цепочке.
            event: Входящее событие (сообщение и т.д.).
            data: Контекст aiogram.

        Returns:
            Результат ``handler`` или ``None``, если доступ запрещён.
        """
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
    """Создаёт ``Dispatcher`` с командами бота.

    Args:
        config: Конфигурация (для списка разрешённых user ID).
        state: Хранилище снимков для ``/status``.
        on_refresh: Async-функция принудительного опроса всех сервисов;
            ``None`` — команда ``/refresh`` отвечает «недоступен».
        history: Хранилище SQLite для ``/chart``; ``None`` — история отключена.

    Returns:
        Настроенный экземпляр ``Dispatcher``.
    """
    dp = Dispatcher()
    auth = AuthMiddleware(set(config.allowed_user_ids))
    dp.message.middleware(auth)
    dp.callback_query.middleware(auth)

    service_names = {s.name for s in config.services}
    service_list = sorted(service_names)

    async def send_chart(
        message: Message,
        service: str,
        period: str,
    ) -> None:
        if history is None:
            await message.answer("История отключена в конфиге (history.enabled).")
            return
        if service not in service_names:
            await message.answer(
                f"Неизвестный сервис «{service}». Доступны: {', '.join(service_list)}"
            )
            return
        if period not in CHART_PERIODS:
            await message.answer(
                f"Неизвестный период «{period}». Доступны: {', '.join(CHART_PERIODS)}"
            )
            return

        result = await render_balance_chart(history, service, period)
        if result is None:
            await message.answer(f"Нет данных для «{service}» за период {period}.")
            return

        png, caption = result
        await message.answer_photo(
            BufferedInputFile(png, filename="chart.png"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        logger.debug("Команда /start: chat_id=%s user_id=%s", message.chat.id, message.from_user.id if message.from_user else None)
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

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        logger.debug("Команда /status: chat_id=%s", message.chat.id)
        statuses = state.all_statuses()
        if not statuses:
            await message.answer("Нет данных. /refresh")
            return

        parts = [format_status_message(name, st) for name, st in sorted(statuses.items())]
        logger.debug("Команда /status: services=%d", len(parts))
        await message.answer("\n\n".join(parts), parse_mode=ParseMode.HTML)

    @dp.message(Command("refresh"))
    async def cmd_refresh(message: Message) -> None:
        logger.debug("Команда /refresh: chat_id=%s", message.chat.id)
        if on_refresh is None:
            await message.answer("Опрос недоступен.")
            return
        await message.answer("Опрос…")
        await on_refresh()
        await cmd_status(message)

    @dp.message(Command("chart"))
    async def cmd_chart(message: Message) -> None:
        logger.debug("Команда /chart: chat_id=%s text=%r", message.chat.id, message.text)
        if history is None:
            await message.answer("История отключена в конфиге (history.enabled).")
            return
        if not service_list:
            await message.answer("В конфиге нет сервисов.")
            return

        service, period = parse_chart_command_args(message.text)
        if service is None:
            await message.answer(
                "Выберите сервис:",
                reply_markup=service_keyboard(service_list),
            )
            return
        if service not in service_names:
            await message.answer(
                f"Неизвестный сервис «{service}». Доступны: {', '.join(service_list)}"
            )
            return
        if period is None:
            service_index = service_list.index(service)
            await message.answer(
                f"Период для <b>{escape_html(service)}</b>:",
                reply_markup=period_keyboard(service_index),
                parse_mode=ParseMode.HTML,
            )
            return
        await send_chart(message, service, period)

    @dp.callback_query(F.data.startswith("chart:s:"))
    async def on_chart_service(callback: CallbackQuery) -> None:
        service_index = parse_service_callback(callback.data or "")
        service = (
            resolve_service_name(service_list, service_index)
            if service_index is not None
            else None
        )
        if service is None:
            await callback.answer("Неизвестный сервис", show_alert=True)
            return
        await callback.answer()
        if callback.message is None:
            return
        await callback.message.edit_text(
            f"Период для <b>{escape_html(service)}</b>:",
            reply_markup=period_keyboard(service_index),
            parse_mode=ParseMode.HTML,
        )

    @dp.callback_query(F.data.startswith("chart:p:"))
    async def on_chart_period(callback: CallbackQuery) -> None:
        if history is None:
            await callback.answer("История отключена", show_alert=True)
            return
        parsed = parse_period_callback(callback.data or "")
        if parsed is None:
            await callback.answer("Некорректный запрос", show_alert=True)
            return
        service_index, period = parsed
        service = resolve_service_name(service_list, service_index)
        if service is None:
            await callback.answer("Неизвестный сервис", show_alert=True)
            return

        await callback.answer("Строю график…")
        if callback.message is None:
            return

        result = await render_balance_chart(history, service, period)
        if result is None:
            await callback.message.answer(
                f"Нет данных для «{service}» за период {period}."
            )
            return

        png, caption = result
        await callback.message.answer_photo(
            BufferedInputFile(png, filename="chart.png"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )

    @dp.message(F.text)
    async def unknown(message: Message) -> None:
        logger.debug("Неизвестная команда/текст: chat_id=%s text=%r", message.chat.id, message.text)
        await message.answer("/status · /refresh · /chart")

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
