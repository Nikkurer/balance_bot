"""Построение графиков баланса и inline-клавиатуры для /chart."""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from balance_bot.history import BalancePoint, HistoryStore
from balance_bot.timezone import get_bot_timezone, to_bot_timezone

logger = logging.getLogger(__name__)

SERVICE_CALLBACK_PREFIX = "chart:s:"
PERIOD_CALLBACK_PREFIX = "chart:p:"

CHART_PERIODS: dict[str, int | None] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "all": None,
}

PERIOD_LABELS: dict[str, str] = {
    "7d": "7 дн",
    "30d": "30 дн",
    "90d": "90 дн",
    "all": "всё",
}


def parse_chart_command_args(text: str | None) -> tuple[str | None, str | None]:
    """Разбирает аргументы ``/chart [service] [period]``.

    Args:
        text: Полный текст сообщения.

    Returns:
        ``(service, period)`` — любой из них может быть ``None``.
    """
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


def period_since(period: str) -> datetime | None:
    """Возвращает нижнюю границу периода в UTC.

    Args:
        period: Ключ из ``CHART_PERIODS``.

    Returns:
        Datetime UTC или ``None`` для ``all``.
    """
    days = CHART_PERIODS.get(period)
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def service_keyboard(service_names: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура выбора сервиса."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for name in sorted(service_names):
        row.append(
            InlineKeyboardButton(
                text=name,
                callback_data=f"{SERVICE_CALLBACK_PREFIX}{name}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def period_keyboard(service: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора периода для сервиса."""
    buttons = [
        InlineKeyboardButton(
            text=PERIOD_LABELS[key],
            callback_data=f"{PERIOD_CALLBACK_PREFIX}{service}:{key}",
        )
        for key in ("7d", "30d", "90d", "all")
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[buttons[:2], buttons[2:]],
    )


def parse_service_callback(data: str) -> str | None:
    """Извлекает имя сервиса из ``chart:s:...``."""
    if not data.startswith(SERVICE_CALLBACK_PREFIX):
        return None
    return data[len(SERVICE_CALLBACK_PREFIX) :]


def parse_period_callback(data: str) -> tuple[str, str] | None:
    """Извлекает ``(service, period)`` из ``chart:p:service:period``."""
    if not data.startswith(PERIOD_CALLBACK_PREFIX):
        return None
    rest = data[len(PERIOD_CALLBACK_PREFIX) :]
    service, _, period = rest.partition(":")
    if not service or period not in CHART_PERIODS:
        return None
    return service, period


def aggregate_points_for_chart(
    points: list[BalancePoint],
    max_points_per_day: int,
) -> list[BalancePoint]:
    """Усредняет точки по суткам, если в БД их больше лимита.

    Если за сутки в БД не больше ``max_points_per_day`` точек — возвращает их
    как есть. При ``max_points_per_day <= 0`` возвращает все точки без изменений.

    Args:
        points: Исходный ряд (по возрастанию ``ts``).
        max_points_per_day: Целевое число точек на графике за сутки.

    Returns:
        Ряд для отрисовки.
    """
    if max_points_per_day <= 0 or not points:
        return points

    by_day: dict[str, list[BalancePoint]] = {}
    for point in points:
        day_key = to_bot_timezone(point.ts).strftime("%Y-%m-%d")
        by_day.setdefault(day_key, []).append(point)

    result: list[BalancePoint] = []
    for day_key in sorted(by_day):
        day_points = sorted(by_day[day_key], key=lambda p: p.ts)
        if len(day_points) <= max_points_per_day:
            result.extend(day_points)
        else:
            result.extend(_average_day_points(day_points, max_points_per_day))
    return result


def _average_day_points(
    day_points: list[BalancePoint],
    buckets: int,
) -> list[BalancePoint]:
    """Делит сутки на ``buckets`` интервалов и усредняет баланс в каждом."""
    total = len(day_points)
    averaged: list[BalancePoint] = []
    for i in range(buckets):
        start = i * total // buckets
        end = (i + 1) * total // buckets
        chunk = day_points[start:end]
        if not chunk:
            continue
        avg_balance = sum(p.balance for p in chunk) / len(chunk)
        averaged.append(
            BalancePoint(
                ts=chunk[-1].ts,
                balance=avg_balance,
                currency=chunk[-1].currency,
            )
        )
    return averaged


def _render_chart_sync(
    service: str,
    points: list[BalancePoint],
    *,
    period: str,
    poll_errors: int,
    chart_points_per_day: int,
) -> tuple[bytes, str]:
    """Строит PNG-график (блокирующий вызов)."""
    display_points = aggregate_points_for_chart(points, chart_points_per_day)
    times = [to_bot_timezone(p.ts) for p in display_points]
    values = [p.balance for p in display_points]
    currency = next((p.currency for p in reversed(display_points) if p.currency), "")

    fig, ax = plt.subplots(figsize=(8, 4), dpi=100)
    try:
        ax.plot(times, values, marker="o", markersize=3, linewidth=1.5)
        ax.set_title(f"{service} — баланс ({PERIOD_LABELS.get(period, period)})")
        ax.set_ylabel(currency or "баланс")
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m", tz=get_bot_timezone()))
        fig.autofmt_xdate()

        if values:
            ax.set_ylim(bottom=0)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)
        png = buf.read()
    finally:
        plt.close(fig)

    period_label = PERIOD_LABELS.get(period, period)
    caption_parts = [
        f"<b>{service}</b>",
        f"Период: {period_label}",
        f"Точек: {len(display_points)}",
    ]
    if poll_errors > 0:
        caption_parts.append(f"Сбоев опроса: {poll_errors}")
    if currency:
        caption_parts.append(f"Валюта: {currency}")
    if values:
        caption_parts.append(f"Сейчас: {values[-1]:g} {currency}".strip())

    return png, " · ".join(caption_parts)


async def render_balance_chart(
    history: HistoryStore,
    service: str,
    period: str,
) -> tuple[bytes, str] | None:
    """Загружает историю и строит PNG.

    Args:
        history: Хранилище SQLite.
        service: Имя сервиса.
        period: Ключ периода (``7d``, ``30d``, ``90d``, ``all``).

    Returns:
        ``(png_bytes, caption_html)`` или ``None``, если точек нет.
    """
    since = period_since(period)
    points = await history.fetch_series(service, since=since)
    if not points:
        return None

    poll_errors = await history.count_poll_errors(service, since=since)
    logger.debug(
        "render_balance_chart: service=%s period=%s points=%d errors=%d",
        service,
        period,
        len(points),
        poll_errors,
    )
    return await asyncio.to_thread(
        _render_chart_sync,
        service,
        points,
        period=period,
        poll_errors=poll_errors,
        chart_points_per_day=history.chart_points_per_day,
    )
