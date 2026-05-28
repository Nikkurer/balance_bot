"""Тесты оценки алертов и форматирования сообщений."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from balance_bot.models import ServiceStatus
from balance_bot.notifications import (
    evaluate_alerts,
    format_alert_message,
    format_status_message,
)
from balance_bot.timezone import set_bot_timezone


class TestEvaluateAlerts:
    def test_error_suppresses_other_alerts(self, make_service) -> None:
        service = make_service(balance_threshold=100.0, subscription_warn_days=7)
        status = ServiceStatus(
            error="api down",
            balance=0.0,
            subscription_end=datetime.now(timezone.utc),
        )
        assert evaluate_alerts(service, status) == {"error"}

    def test_low_balance_when_below_threshold(self, make_service) -> None:
        service = make_service(balance_threshold=100.0)
        status = ServiceStatus(balance=50.0)
        assert evaluate_alerts(service, status) == {"low_balance"}

    def test_no_low_balance_when_threshold_not_set(self, make_service) -> None:
        service = make_service(balance_threshold=None)
        status = ServiceStatus(balance=0.0)
        assert evaluate_alerts(service, status) == set()

    def test_no_low_balance_when_balance_unknown(self, make_service) -> None:
        service = make_service(balance_threshold=100.0)
        status = ServiceStatus(balance=None)
        assert evaluate_alerts(service, status) == set()

    def test_subscription_ending_within_warn_window(
        self, make_service, utc_now: datetime
    ) -> None:
        service = make_service(subscription_warn_days=7)
        end = utc_now + timedelta(days=3)
        status = ServiceStatus(subscription_end=end)

        with patch("balance_bot.notifications._utc_now", return_value=utc_now):
            assert evaluate_alerts(service, status) == {"subscription_ending"}

    def test_subscription_naive_datetime_treated_as_utc(
        self, make_service, utc_now: datetime
    ) -> None:
        service = make_service(subscription_warn_days=7)
        end = datetime(2026, 5, 28, 0, 0, 0)  # naive, within 7 days of utc_now
        status = ServiceStatus(subscription_end=end)

        with patch("balance_bot.notifications._utc_now", return_value=utc_now):
            assert "subscription_ending" in evaluate_alerts(service, status)

    def test_subscription_not_ending_when_beyond_warn_window(
        self, make_service, utc_now: datetime
    ) -> None:
        service = make_service(subscription_warn_days=7)
        end = utc_now + timedelta(days=30)
        status = ServiceStatus(subscription_end=end)

        with patch("balance_bot.notifications._utc_now", return_value=utc_now):
            assert evaluate_alerts(service, status) == set()

    def test_combined_low_balance_and_subscription(
        self, make_service, utc_now: datetime
    ) -> None:
        service = make_service(balance_threshold=100.0, subscription_warn_days=14)
        status = ServiceStatus(
            balance=10.0,
            subscription_end=utc_now + timedelta(days=1),
        )

        with patch("balance_bot.notifications._utc_now", return_value=utc_now):
            assert evaluate_alerts(service, status) == {
                "low_balance",
                "subscription_ending",
            }


class TestFormatMessages:
    def test_format_status_with_error(self) -> None:
        text = format_status_message("svc", ServiceStatus(error="timeout"))
        assert "<b>svc</b>" in text
        assert "timeout" in text
        assert "❌" in text

    def test_format_status_with_balance_and_subscription(self) -> None:
        set_bot_timezone("Europe/Moscow")
        status = ServiceStatus(
            balance=42.5,
            currency="RUB",
            subscription_end=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
            last_updated=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
        )
        text = format_status_message("my-vps", status)
        assert "42.5" in text
        assert "RUB" in text
        assert "2026-06-01 03:00" in text
        assert "MSK" in text

    @pytest.mark.parametrize(
        ("alert", "fragment"),
        [
            ("low_balance", "низкий баланс"),
            ("subscription_ending", "подписка заканчивается"),
            ("error", "ошибка опроса"),
        ],
    )
    def test_format_alert_known_types(self, alert: str, fragment: str) -> None:
        status = ServiceStatus(
            balance=1.0,
            currency="USD",
            subscription_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
            error="fail",
        )
        text = format_alert_message("svc", alert, status)
        assert fragment in text
        assert "<b>svc</b>" in text

    def test_format_alert_unknown_type_fallback(self) -> None:
        text = format_alert_message("svc", "custom", ServiceStatus())
        assert "custom" in text

    def test_format_alert_subscription_uses_bot_timezone(self) -> None:
        set_bot_timezone("Europe/Moscow")
        status = ServiceStatus(subscription_end=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))
        text = format_alert_message("svc", "subscription_ending", status)
        assert "2026-01-01 03:00" in text
