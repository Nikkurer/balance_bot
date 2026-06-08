"""Тесты форматирования HTTP-ошибок."""

from balance_bot.http_errors import format_http_error_body


def test_format_http_error_body_from_html_title() -> None:
    body = "<html><head><title>504 Gateway Timeout</title></head><body></body></html>"
    assert format_http_error_body(504, body, reason="Gateway Timeout") == "504 Gateway Timeout"


def test_format_http_error_body_html_without_title_uses_reason() -> None:
    body = "<html><body><h1>Error</h1></body></html>"
    assert format_http_error_body(502, body, reason="Bad Gateway") == "Bad Gateway"


def test_format_http_error_body_from_json() -> None:
    body = '{"status_msg": "Internal error"}'
    assert (
        format_http_error_body(500, body, content_type="application/json")
        == "Internal error"
    )


def test_format_http_error_body_empty_uses_reason() -> None:
    assert format_http_error_body(503, "", reason="Service Unavailable") == "Service Unavailable"
