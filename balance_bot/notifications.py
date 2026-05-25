from datetime import datetime, timedelta, timezone

from balance_bot.models import ServiceConfig, ServiceStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def evaluate_alerts(service: ServiceConfig, status: ServiceStatus) -> set[str]:
    """Определить активные предупреждения по текущему снимку состояния."""
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


def format_status_message(service_name: str, status: ServiceStatus) -> str:
    lines = [f"<b>{service_name}</b>"]

    if status.error:
        lines.append(f"❌ Ошибка: {status.error}")
        return "\n".join(lines)

    if status.balance is not None:
        currency = status.currency or ""
        lines.append(f"💰 Баланс: {status.balance:g} {currency}".strip())

    if status.subscription_end is not None:
        end = status.subscription_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        lines.append(f"📅 Подписка до: {end.strftime('%Y-%m-%d %H:%M UTC')}")

    if status.last_updated:
        lines.append(f"🕐 Обновлено: {status.last_updated.strftime('%Y-%m-%d %H:%M UTC')}")

    return "\n".join(lines)


def format_alert_message(service_name: str, alert: str, status: ServiceStatus) -> str:
    if alert == "low_balance":
        currency = status.currency or ""
        return (
            f"⚠️ <b>{service_name}</b>: низкий баланс — "
            f"{status.balance:g} {currency}".strip()
        )
    if alert == "subscription_ending":
        end = status.subscription_end
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        end_str = end.strftime("%Y-%m-%d %H:%M UTC") if end else "неизвестно"
        return f"⚠️ <b>{service_name}</b>: подписка заканчивается {end_str}"
    if alert == "error":
        return f"❌ <b>{service_name}</b>: ошибка опроса — {status.error}"
    return f"⚠️ <b>{service_name}</b>: {alert}"
