"""Утилиты работы с часовой зоной бота."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_BOT_TIMEZONE = ZoneInfo("UTC")
_BOT_TIMEZONE_NAME = "UTC"


def set_bot_timezone(timezone_name: str) -> None:
    """Устанавливает глобальную timezone для сообщений и логов.

    Args:
        timezone_name: IANA timezone (например, ``Europe/Moscow``).
    """
    global _BOT_TIMEZONE, _BOT_TIMEZONE_NAME
    _BOT_TIMEZONE = ZoneInfo(timezone_name)
    _BOT_TIMEZONE_NAME = timezone_name


def get_bot_timezone() -> ZoneInfo:
    """Возвращает текущую timezone бота."""
    return _BOT_TIMEZONE


def get_bot_timezone_name() -> str:
    """Возвращает имя текущей timezone бота."""
    return _BOT_TIMEZONE_NAME


def to_bot_timezone(dt: datetime) -> datetime:
    """Приводит datetime к timezone бота.

    Naive datetime трактуется как UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BOT_TIMEZONE)
