"""Настройка логов: понятные сообщения для Telegram/aiogram и сетевых сбоев."""

from __future__ import annotations

import logging
import re

# Логгеры aiogram, чьи сообщения переписываем
_AIOGRAM_LOGGER_PREFIXES = ("aiogram",)

_SLEEP_RE = re.compile(
    r"Sleep for (?P<sec>[\d.]+) seconds? and try again.*?tryings\s*=\s*(?P<try>\d+)",
    re.IGNORECASE,
)
_CONNECTED_RE = re.compile(
    r"Connection established.*?tryings\s*=\s*(?P<try>\d+)",
    re.IGNORECASE,
)


def _humanize_network_error(exc: BaseException) -> str:
    name = type(exc).__name__
    text = str(exc).lower()

    if "timeout" in text or name == "TimeoutError":
        return (
            "таймаут запроса к Telegram — проверьте интернет и доступ к api.telegram.org; "
            "бот повторит подключение сам"
        )
    if "connector" in text or "connection" in text or "network" in name.lower():
        return (
            "сбой сети при обращении к Telegram — временная проблема связи, "
            "бот повторит подключение сам"
        )
    if "server disconnected" in text:
        return "Telegram разорвал соединение — бот переподключится автоматически"
    return f"{name}: {exc}"


def humanize_log_message(message: str) -> str | None:
    """Вернуть понятный текст или None, если сообщение не трогаем."""
    if "Failed to fetch updates" in message:
        if "timeout" in message.lower():
            return (
                "Telegram: не удалось получить обновления — таймаут. "
                "Проверьте сеть и доступ к api.telegram.org; повтор через несколько секунд"
            )
        if "TelegramNetworkError" in message or "ClientConnectorError" in message:
            detail = message.split(" - ", 1)[-1].strip()
            return f"Telegram: не удалось получить обновления — {detail}"
        return "Telegram: не удалось получить обновления — временный сбой связи"

    m = _SLEEP_RE.search(message)
    if m:
        sec = float(m.group("sec"))
        attempt = int(m.group("try")) + 1
        sec_s = f"{sec:g}" if sec < 10 else f"{sec:.0f}"
        return (
            f"Telegram: пауза {sec_s} с перед повтором подключения (попытка {attempt})"
        )

    m = _CONNECTED_RE.search(message)
    if m:
        attempt = int(m.group("try")) + 1
        return f"Telegram: подключение восстановлено (попытка {attempt})"

    if message.startswith("Failed to send notification"):
        return message.replace(
            "Failed to send notification",
            "Не удалось отправить уведомление пользователю",
            1,
        )

    return None


def _is_transient_telegram_failure(message: str) -> bool:
    return "Failed to fetch updates" in message and (
        "timeout" in message.lower()
        or "TelegramNetworkError" in message
        or "ClientConnector" in message
    )


class HumanizeLogFilter(logging.Filter):
    """Переписывает типовые сообщения aiogram на русский; снижает уровень ожидаемых сбоев."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith(_AIOGRAM_LOGGER_PREFIXES):
            return True

        original = record.getMessage()
        friendly = humanize_log_message(original)

        if friendly is None and record.exc_info:
            exc = record.exc_info[1]
            if exc is not None:
                friendly = (
                    "Telegram: не удалось получить обновления — "
                    + _humanize_network_error(exc)
                )

        if friendly:
            record.msg = friendly
            record.args = ()
            if _is_transient_telegram_failure(original) and record.levelno == logging.ERROR:
                record.levelno = logging.WARNING
                record.levelname = "WARNING"
            if record.levelno < logging.DEBUG:
                record.exc_info = None
                record.exc_text = None

        return True


def setup_logging(*, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)

    for h in root.handlers:
        h.addFilter(HumanizeLogFilter())

    # Шумные отладочные логи aiogram — только с -v
    logging.getLogger("aiogram.event").setLevel(logging.DEBUG if verbose else logging.WARNING)
