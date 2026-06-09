"""Отрисовка графика баланса в PNG (matplotlib — lazy import)."""

from __future__ import annotations

import asyncio
import io
import logging

from balance_bot.chart_data import PERIOD_LABELS, aggregate_points_for_chart, period_since
from balance_bot.history import BalancePoint, HistoryStore
from balance_bot.notifications import escape_html
from balance_bot.timezone import get_bot_timezone, to_bot_timezone

logger = logging.getLogger(__name__)


def render_chart_sync(
    service: str,
    points: list[BalancePoint],
    *,
    period: str,
    poll_errors: int,
    chart_points_per_day: int,
) -> tuple[bytes, str]:
    """Строит PNG-график (блокирующий вызов)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

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
        f"<b>{escape_html(service)}</b>",
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
    """Загружает историю и строит PNG."""
    since = period_since(period)
    chart_data = await history.fetch_chart_data(service, since=since)
    if not chart_data.points:
        return None

    logger.debug(
        "render_balance_chart: service=%s period=%s points=%d errors=%d",
        service,
        period,
        len(chart_data.points),
        chart_data.error_count,
    )
    return await asyncio.to_thread(
        render_chart_sync,
        service,
        chart_data.points,
        period=period,
        poll_errors=chart_data.error_count,
        chart_points_per_day=history.chart_points_per_day,
    )
