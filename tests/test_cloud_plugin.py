"""Unit-тесты служебных функций плагина Cloud.ru."""

from datetime import datetime, timezone

from plugins.cloud import _aggregate_grants
from balance_bot.plugins.http_client import extract_trace_id as cloud_extract_trace_id
from plugins.cloud import _pick_active_grants


def test_cloud_extract_trace_id_from_correlation_id() -> None:
    payload = {"correlationId": "corr-1"}
    assert cloud_extract_trace_id(payload) == "corr-1"


def test_cloud_pick_active_grants_filters_ready_only() -> None:
    payload = {
        "bonus_grants": [
            {"status": "BONUS_GRANT_STATUS_READY", "current_amount": "10"},
            {"status": "BONUS_GRANT_STATUS_NOT_STARTED", "current_amount": "20"},
        ]
    }
    active = _pick_active_grants(payload)
    assert len(active) == 1
    assert active[0]["current_amount"] == "10"


def test_cloud_aggregate_grants_sums_amounts_and_picks_nearest_expire() -> None:
    grants = [
        {
            "current_amount": "100.5",
            "expire_at": "2026-12-01T00:00:00Z",
        },
        {
            "current_amount": "50",
            "expire_at": "2026-06-01T00:00:00Z",
        },
    ]
    balance, expire = _aggregate_grants(grants)
    assert balance == 150.5
    assert expire == datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
