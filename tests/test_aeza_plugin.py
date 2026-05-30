"""Тесты служебных функций HTTP-плагинов."""

from plugins.aeza import _api_message as aeza_api_message
from plugins.aeza import _extract_trace_id as aeza_extract_trace_id
from plugins.aeza import _parse_balance
from plugins.cloud import _extract_trace_id as cloud_extract_trace_id


def test_aeza_api_message_prefers_error_message() -> None:
    payload = {"error": {"message": "Proxy internal server error", "slug": "proxy_error"}}
    assert aeza_api_message(payload) == "Proxy internal server error"


def test_aeza_api_message_fallback_to_status_message() -> None:
    payload = {"status_msg": "Something wrong"}
    assert aeza_api_message(payload) == "Something wrong"


def test_aeza_extract_trace_id_from_root() -> None:
    payload = {"traceId": "abc-123"}
    assert aeza_extract_trace_id(payload) == "abc-123"


def test_aeza_extract_trace_id_from_error_object() -> None:
    payload = {"error": {"request_id": "req-777"}}
    assert aeza_extract_trace_id(payload) == "req-777"


def test_aeza_extract_trace_id_returns_none_for_unknown_payload() -> None:
    assert aeza_extract_trace_id({"error": {"message": "boom"}}) is None


def test_cloud_extract_trace_id_from_correlation_id() -> None:
    payload = {"correlationId": "corr-1"}
    assert cloud_extract_trace_id(payload) == "corr-1"


def test_aeza_parse_balance_converts_kopecks_to_rubles() -> None:
    balance, currency = _parse_balance(
        {"balance": {"value": 12345, "currency": "RUB"}}
    )
    assert balance == 123.45
    assert currency == "RUB"


def test_aeza_parse_balance_uses_round_field() -> None:
    balance, _ = _parse_balance({"balance": {"value": 1000, "round": 3}})
    assert balance == 1.0


def test_aeza_parse_balance_scalar_minor_units() -> None:
    balance, currency = _parse_balance({"balance": 5000})
    assert balance == 50.0
    assert currency is None
