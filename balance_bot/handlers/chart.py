"""Обработчики /chart и inline-кнопок графика."""

import logging

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from balance_bot.chart_data import CHART_PERIODS
from balance_bot.chart_render import render_balance_chart
from balance_bot.chart_ui import (
    parse_chart_command_args,
    parse_period_callback,
    parse_service_callback,
    period_keyboard,
    resolve_service_name,
    service_keyboard,
)
from balance_bot.history import HistoryStore
from balance_bot.models import AppConfig
from balance_bot.notifications import escape_html

logger = logging.getLogger(__name__)


async def send_chart_photo(message: Message, png: bytes, caption: str) -> None:
    """Отправляет PNG-график с HTML-подписью."""
    await message.answer_photo(
        BufferedInputFile(png, filename="chart.png"),
        caption=caption,
        parse_mode=ParseMode.HTML,
    )


def register_chart_handlers(
    dp: Dispatcher,
    *,
    config: AppConfig,
    history: HistoryStore | None,
) -> None:
    """Регистрирует ``/chart`` и callback ``chart:s:`` / ``chart:p:``."""
    service_names = {s.name for s in config.services}
    service_list = sorted(service_names)

    async def send_chart(message: Message, service: str, period: str) -> None:
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
        await send_chart_photo(message, png, caption)

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
        await send_chart_photo(callback.message, png, caption)
