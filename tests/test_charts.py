"""Тесты графиков и разбора /chart."""

from datetime import datetime, timedelta, timezone

from balance_bot.chart_data import aggregate_points_for_chart, period_since
from balance_bot.chart_render import render_chart_sync
from balance_bot.chart_ui import (
    callback_data_fits,
    parse_chart_command_args,
    parse_period_callback,
    parse_service_callback,
    period_callback_data,
    resolve_service_name,
    service_callback_data,
    service_keyboard,
)
from balance_bot.history import BalancePoint


def test_parse_chart_command_args() -> None:
    assert parse_chart_command_args("/chart") == (None, None)
    assert parse_chart_command_args("/chart vdsina-ru") == ("vdsina-ru", None)
    assert parse_chart_command_args("/chart vdsina-ru 30d") == ("vdsina-ru", "30d")
    assert parse_chart_command_args("/chart vdsina-ru bad") == ("vdsina-ru", None)


def test_parse_service_callback_index() -> None:
    assert parse_service_callback("chart:s:0") == 0
    assert parse_service_callback("chart:s:12") == 12
    assert parse_service_callback("chart:p:0:7d") is None
    assert parse_service_callback("chart:s:-1") is None
    assert parse_service_callback("chart:s:x") is None


def test_parse_period_callback_index() -> None:
    assert parse_period_callback("chart:p:2:30d") == (2, "30d")
    assert parse_period_callback("chart:p:0:all") == (0, "all")
    assert parse_period_callback("chart:p:0:bad") is None


def test_callback_data_within_telegram_limit() -> None:
    assert callback_data_fits(service_callback_data(0))
    assert callback_data_fits(period_callback_data(99, "30d"))


def test_resolve_service_name() -> None:
    names = ["b", "a", "c"]
    assert resolve_service_name(names, 0) == "a"
    assert resolve_service_name(names, 2) == "c"
    assert resolve_service_name(names, 5) is None


def test_service_keyboard_uses_indices() -> None:
    markup = service_keyboard(["b", "a", "c"])
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert callbacks == ["chart:s:0", "chart:s:1", "chart:s:2"]
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert texts == ["a", "b", "c"]


def test_period_since_all_is_none() -> None:
    assert period_since("all") is None
    assert period_since("7d") is not None


def test_aggregate_keeps_raw_points_when_below_limit() -> None:
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    points = [
        BalancePoint(ts=base, balance=10.0, currency="RUB"),
        BalancePoint(ts=base + timedelta(hours=6), balance=20.0, currency="RUB"),
    ]
    result = aggregate_points_for_chart(points, max_points_per_day=4)
    assert result == points


def test_aggregate_averages_when_above_limit() -> None:
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    points = [
        BalancePoint(ts=base + timedelta(hours=i), balance=float(i), currency="RUB")
        for i in range(24)
    ]
    result = aggregate_points_for_chart(points, max_points_per_day=4)
    assert len(result) == 4
    assert result[0].balance == 2.5  # avg of 0,1,2,3,4,5
    assert result[-1].balance == 20.5  # avg of 18..23


def test_aggregate_disabled_when_zero() -> None:
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    points = [
        BalancePoint(ts=base + timedelta(minutes=i), balance=float(i), currency="RUB")
        for i in range(50)
    ]
    result = aggregate_points_for_chart(points, max_points_per_day=0)
    assert len(result) == 50


def test_render_chart_sync_produces_png() -> None:
    points = [
        BalancePoint(
            ts=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
            balance=100.0,
            currency="RUB",
        ),
        BalancePoint(
            ts=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
            balance=90.0,
            currency="RUB",
        ),
    ]
    png, caption = render_chart_sync(
        "svc",
        points,
        period="7d",
        poll_errors=2,
        chart_points_per_day=0,
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert "svc" in caption
    assert "Сбоев опроса: 2" in caption
