"""Периоды и агрегация точек баланса для графиков."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from balance_bot.history import BalancePoint
from balance_bot.timezone import to_bot_timezone

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


def period_since(period: str) -> datetime | None:
    """Возвращает нижнюю границу периода в UTC."""
    days = CHART_PERIODS.get(period)
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def aggregate_points_for_chart(
    points: list[BalancePoint],
    max_points_per_day: int,
) -> list[BalancePoint]:
    """Усредняет точки по суткам, если в БД их больше лимита."""
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
