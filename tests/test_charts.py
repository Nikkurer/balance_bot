"""Тесты графиков и разбора /chart."""

from datetime import datetime, timedelta, timezone

from balance_bot.charts import (
    downsample_points,
    parse_chart_command_args,
    parse_period_callback,
    parse_service_callback,
    period_since,
    service_keyboard,
)
from balance_bot.history import BalancePoint


def test_parse_chart_command_args() -> None:
    assert parse_chart_command_args("/chart") == (None, None)
    assert parse_chart_command_args("/chart vdsina-ru") == ("vdsina-ru", None)
    assert parse_chart_command_args("/chart vdsina-ru 30d") == ("vdsina-ru", "30d")
    assert parse_chart_command_args("/chart vdsina-ru bad") == ("vdsina-ru", None)


def test_parse_service_callback() -> None:
    assert parse_service_callback("chart:s:cloud-main") == "cloud-main"
    assert parse_service_callback("chart:p:x:7d") is None


def test_parse_period_callback() -> None:
    assert parse_period_callback("chart:p:vdsina-ru:30d") == ("vdsina-ru", "30d")
    assert parse_period_callback("chart:p:vdsina-ru:bad") is None


def test_service_keyboard_layout() -> None:
    markup = service_keyboard(["b", "a", "c"])
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert texts == ["a", "b", "c"]


def test_period_since_all_is_none() -> None:
    assert period_since("all") is None
    assert period_since("7d") is not None


def test_downsample_collapses_to_daily() -> None:
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    points = [
        BalancePoint(
            ts=base + timedelta(minutes=i),
            balance=float(i),
            currency="RUB",
        )
        for i in range(250)
    ]
    result = downsample_points(points)
    assert len(result) == 1
    assert result[0].balance == 249.0


def test_render_chart_sync_produces_png() -> None:
    from balance_bot.charts import _render_chart_sync

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
    png, caption = _render_chart_sync("svc", points, period="7d", poll_errors=2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert "svc" in caption
    assert "Сбоев опроса: 2" in caption
