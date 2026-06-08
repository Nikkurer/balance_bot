"""Форматирование тел HTTP-ошибок для сообщений пользователю и логов."""

from __future__ import annotations

import json
import re
from html import unescape

_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def format_http_error_body(
    status: int,
    body: str,
    *,
    content_type: str | None = None,
    reason: str | None = None,
) -> str:
    """Возвращает короткое описание ошибки без сырого HTML.

    Args:
        status: HTTP-код ответа.
        body: Тело ответа.
        content_type: Заголовок ``Content-Type``.
        reason: ``resp.reason`` (например ``Gateway Timeout``).

    Returns:
        Текст для ``ServiceStatus.error`` / исключения API.
    """
    text = body.strip()
    if not text:
        return reason or f"HTTP {status}"

    ct = (content_type or "").lower()
    if "html" in ct or text.lstrip().startswith("<"):
        if title := _extract_html_title(text):
            return title
        return reason or f"HTTP {status}"

    if "json" in ct or text.startswith("{") or text.startswith("["):
        if msg := _json_error_message(text):
            return msg

    one_line = " ".join(text.split())
    if len(one_line) > 200:
        return one_line[:200] + "…"
    return one_line


def _extract_html_title(html: str) -> str | None:
    match = _HTML_TITLE_RE.search(html)
    if not match:
        return None
    title = unescape(match.group(1)).strip()
    return title or None


def _json_error_message(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("message", "status_msg", "detail", "description", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("message") or value.get("detail")
            if nested:
                return str(nested).strip()
    return None
