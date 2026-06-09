"""Inline-клавиатуры и разбор callback для /chart."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from balance_bot.chart_data import CHART_PERIODS, PERIOD_LABELS

SERVICE_CALLBACK_PREFIX = "chart:s:"
PERIOD_CALLBACK_PREFIX = "chart:p:"
TELEGRAM_CALLBACK_DATA_MAX_BYTES = 64


def parse_chart_command_args(text: str | None) -> tuple[str | None, str | None]:
    """Разбирает аргументы ``/chart [service] [period]``."""
    if not text:
        return None, None
    parts = text.split(maxsplit=2)
    if len(parts) < 2:
        return None, None
    service = parts[1]
    period = parts[2] if len(parts) > 2 else None
    if period is not None and period not in CHART_PERIODS:
        return service, None
    return service, period


def callback_data_fits(data: str) -> bool:
    """Проверяет, что ``callback_data`` укладывается в лимит Telegram (64 байта)."""
    return len(data.encode("utf-8")) <= TELEGRAM_CALLBACK_DATA_MAX_BYTES


def service_callback_data(index: int) -> str:
    """Формирует ``callback_data`` выбора сервиса по индексу."""
    data = f"{SERVICE_CALLBACK_PREFIX}{index}"
    if not callback_data_fits(data):
        raise ValueError(f"callback_data слишком длинный: {data!r}")
    return data


def period_callback_data(service_index: int, period: str) -> str:
    """Формирует ``callback_data`` выбора периода."""
    data = f"{PERIOD_CALLBACK_PREFIX}{service_index}:{period}"
    if not callback_data_fits(data):
        raise ValueError(f"callback_data слишком длинный: {data!r}")
    return data


def resolve_service_name(service_names: list[str], index: int) -> str | None:
    """Возвращает имя сервиса по индексу в отсортированном списке."""
    ordered = sorted(service_names)
    if index < 0 or index >= len(ordered):
        return None
    return ordered[index]


def service_keyboard(service_names: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура выбора сервиса (индекс в ``callback_data``, имя на кнопке)."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, name in enumerate(sorted(service_names)):
        row.append(
            InlineKeyboardButton(
                text=name,
                callback_data=service_callback_data(index),
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def period_keyboard(service_index: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора периода для сервиса."""
    buttons = [
        InlineKeyboardButton(
            text=PERIOD_LABELS[key],
            callback_data=period_callback_data(service_index, key),
        )
        for key in ("7d", "30d", "90d", "all")
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[buttons[:2], buttons[2:]],
    )


def parse_service_callback(data: str) -> int | None:
    """Извлекает индекс сервиса из ``chart:s:<index>``."""
    if not data.startswith(SERVICE_CALLBACK_PREFIX):
        return None
    rest = data[len(SERVICE_CALLBACK_PREFIX) :]
    try:
        index = int(rest)
    except ValueError:
        return None
    return index if index >= 0 else None


def parse_period_callback(data: str) -> tuple[int, str] | None:
    """Извлекает ``(service_index, period)`` из ``chart:p:<index>:<period>``."""
    if not data.startswith(PERIOD_CALLBACK_PREFIX):
        return None
    rest = data[len(PERIOD_CALLBACK_PREFIX) :]
    index_str, _, period = rest.partition(":")
    if not index_str or period not in CHART_PERIODS:
        return None
    try:
        index = int(index_str)
    except ValueError:
        return None
    if index < 0:
        return None
    return index, period
