"""Тесты humanize-фильтра логов."""

import logging

from balance_bot.logging_setup import (
    BotTimezoneFormatter,
    HumanizeLogFilter,
    humanize_log_message,
)


def test_humanize_failed_fetch_updates_timeout() -> None:
    msg = humanize_log_message("Failed to fetch updates - TelegramNetworkError timeout")
    assert msg is not None
    assert "таймаут" in msg.lower() or "обновления" in msg.lower()


def test_humanize_sleep_message() -> None:
    msg = humanize_log_message("Sleep for 1.5 seconds and try again... tryings = 2")
    assert msg is not None
    assert "пауза" in msg


def test_humanize_unknown_message_returns_none() -> None:
    assert humanize_log_message("some random log line") is None


def test_filter_lowers_transient_error_to_warning() -> None:
    filt = HumanizeLogFilter()
    record = logging.LogRecord(
        name="aiogram.dispatcher",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="Failed to fetch updates - TelegramNetworkError timeout",
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is True
    assert record.levelno == logging.WARNING
    assert "Telegram" in record.getMessage()


def test_bot_timezone_formatter_uses_selected_zone() -> None:
    formatter = BotTimezoneFormatter("%(asctime)s %(message)s", "Europe/Moscow")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.created = 0
    rendered = formatter.format(record)
    assert "1970-01-01 03:00:00" in rendered
