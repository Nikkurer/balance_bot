"""Тесты служебных функций HTTP-плагинов."""

from plugins.aeza import _api_message as aeza_api_message
from plugins.aeza import _extract_trace_id as aeza_extract_trace_id
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
