"""Оценка алертов и форматирование сообщений для Telegram (HTML)."""

from datetime import datetime, timedelta, timezone

from balance_bot.models import ServiceConfig, ServiceStatus
from balance_bot.timezone import to_bot_timezone


def _utc_now() -> datetime:
    """Возвращает текущее время в UTC.

    Returns:
        Осознанный ``datetime`` с ``timezone.utc``.
    """
    return datetime.now(timezone.utc)


def evaluate_alerts(service: ServiceConfig, status: ServiceStatus) -> set[str]:
    """Определяет активные предупреждения по текущему снимку состояния.

    При ``status.error`` возвращается только ``{"error"}`` — пороги баланса
    и подписки не проверяются.

    Args:
        service: Конфигурация сервиса с порогами.
        status: Снимок после опроса плагина.

    Returns:
        Множество идентификаторов алертов: ``low_balance``,
        ``subscription_ending``, ``error``.
    """
    alerts: set[str] = set()

    if status.error:
        alerts.add("error")
        return alerts

    if (
        service.balance_threshold is not None
        and status.balance is not None
        and status.balance < service.balance_threshold
    ):
        alerts.add("low_balance")

    if service.subscription_warn_days is not None and status.subscription_end is not None:
        warn_before = _utc_now() + timedelta(days=service.subscription_warn_days)
        end = status.subscription_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end <= warn_before:
            alerts.add("subscription_ending")

    return alerts


def _format_dt(dt: datetime) -> str:
    """Компактная дата/время в timezone бота."""
    local = to_bot_timezone(dt)
    return local.strftime("%d.%m.%Y %H:%M %Z")


def format_status_message(service_name: str, status: ServiceStatus) -> str:
    """Форматирует блок статуса одного сервиса для команды ``/status``.

    Args:
        service_name: Отображаемое имя сервиса.
        status: Снимок из ``StateStore``.

    Returns:
        HTML-текст (жирное имя, эмодзи-метки).
    """
    lines = [f"<b>{service_name}</b>"]

    if status.error:
        lines.append(f"❌ {status.error}")
        return "\n".join(lines)

    parts: list[str] = []
    if status.balance is not None:
        currency = status.currency or ""
        parts.append(f"💰 {status.balance:g} {currency}".strip())

    subscription_display = status.details.get("subscription_end_display")
    if subscription_display is not None:
        parts.append(f"📅 {subscription_display}")
    elif status.subscription_end is not None:
        parts.append(f"📅 {_format_dt(status.subscription_end)}")

    if parts:
        lines.append(" · ".join(parts))

    if status.last_updated:
        lines.append(f"🕐 {_format_dt(status.last_updated)}")

    return "\n".join(lines)


def format_alert_message(service_name: str, alert: str, status: ServiceStatus) -> str:
    """Форматирует push-уведомление при появлении нового алерта.

    Args:
        service_name: Имя сервиса из конфига.
        alert: Тип алерта (``low_balance``, ``subscription_ending``, ``error``).
        status: Снимок, на основе которого сработал алерт.

    Returns:
        HTML-текст для ``send_message``.
    """
    if alert == "low_balance":
        currency = status.currency or ""
        amount = f"{status.balance:g} {currency}".strip()
        return f"⚠️ <b>{service_name}</b> · баланс {amount}"
    if alert == "subscription_ending":
        end = status.subscription_end
        end_str = _format_dt(end) if end else "?"
        return f"⚠️ <b>{service_name}</b> · подписка до {end_str}"
    if alert == "error":
        return f"❌ <b>{service_name}</b> · {status.error}"
    return f"⚠️ <b>{service_name}</b> · {alert}"
