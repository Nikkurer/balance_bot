"""Общий HTTP-клиент и утилиты для плагинов провайдеров."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

import aiohttp

from balance_bot.http_errors import format_http_error_body

logger = logging.getLogger(__name__)

_TRACE_ID_KEYS = (
    "traceId",
    "trace_id",
    "requestId",
    "request_id",
    "correlationId",
)


class PluginApiError(Exception):
    """Базовая ошибка ответа или логики API провайдера."""


class PluginHttpClient:
    """Ленивая ``aiohttp.ClientSession`` с единым разбором HTTP/JSON ошибок."""

    def __init__(
        self,
        *,
        error_class: type[PluginApiError],
        log_prefix: str,
        service_name: str,
        timeout_seconds: float = 30,
    ) -> None:
        self._error_class = error_class
        self._log_prefix = log_prefix
        self._service_name = service_name
        self._timeout_seconds = timeout_seconds
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        """Закрывает HTTP-сессию."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        message_extractor: Any | None = None,
    ) -> dict:
        """Выполняет HTTP-запрос и возвращает JSON-объект.

        Args:
            method: HTTP-метод.
            url: Полный URL.
            headers: Заголовки запроса.
            params: Query-параметры (GET).
            json_body: Тело POST (``application/json``).
            message_extractor: Опциональная функция ``(payload) -> str | None``
                для текста ошибки из JSON при HTTP ≥400.

        Returns:
            Распарсенный объект-словарь.

        Raises:
            PluginApiError: HTTP ≥400, не JSON или неожиданный формат.
        """
        req_headers = dict(headers or {})
        if json_body is not None:
            req_headers.setdefault("Content-Type", "application/json")

        session = self._ensure_session()
        logger.debug(
            "%s: %s %s service=%s params=%s has_body=%s",
            self._log_prefix,
            method,
            url,
            self._service_name,
            params,
            json_body is not None,
        )
        async with session.request(
            method,
            url,
            headers=req_headers,
            params=params,
            json=json_body,
        ) as resp:
            logger.debug(
                "%s: %s %s -> HTTP %s",
                self._log_prefix,
                method,
                url,
                resp.status,
            )
            if resp.status >= 400:
                text = await resp.text()
                msg = format_http_error_body(
                    resp.status,
                    text,
                    content_type=resp.headers.get("Content-Type"),
                    reason=resp.reason,
                )
                payload: dict | None = None
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        payload = parsed
                        extract = message_extractor or api_message
                        msg = extract(payload) or msg
                except json.JSONDecodeError:
                    pass

                trace_id = resp.headers.get("x-trace-id")
                request_id = resp.headers.get("x-request-id")
                if payload is not None:
                    trace_id = extract_trace_id(payload) or trace_id

                suffix = _format_error_suffix(trace_id=trace_id, request_id=request_id)
                logger.error(
                    "%s: HTTP %s %s %s — %s%s (service=%s)",
                    self._log_prefix,
                    resp.status,
                    method,
                    url,
                    msg,
                    suffix,
                    self._service_name,
                )
                raise self._error_class(
                    f"{url}: HTTP {resp.status} — {msg}{suffix}"
                )

            try:
                payload = await resp.json(content_type=None)
            except Exception as exc:
                raise self._error_class(
                    f"{url}: ответ не JSON (HTTP {resp.status})"
                ) from exc

            if not isinstance(payload, dict):
                raise self._error_class(f"{url}: неожиданный формат ответа")

            return payload

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout_seconds)
            )
        return self._session


def _format_error_suffix(
    *,
    trace_id: str | None,
    request_id: str | None,
) -> str:
    parts: list[str] = []
    if trace_id:
        parts.append(f"traceId={trace_id}")
    if request_id:
        parts.append(f"requestId={request_id}")
    return f" ({', '.join(parts)})" if parts else ""


def api_message(payload: dict | None) -> str | None:
    """Извлекает текст ошибки из JSON ответа API провайдера."""
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict):
        for key in ("message", "slug", "detail", "code"):
            value = err.get(key)
            if value:
                return str(value)
    if isinstance(err, str) and err.strip():
        return err.strip()
    for key in ("status_msg", "message", "detail", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_trace_id(payload: dict | None) -> str | None:
    """Извлекает trace/request/correlation id из JSON ответа."""
    if not isinstance(payload, dict):
        return None
    for key in _TRACE_ID_KEYS:
        value = payload.get(key)
        if value:
            return str(value)
    err = payload.get("error")
    if isinstance(err, dict):
        for key in _TRACE_ID_KEYS:
            value = err.get(key)
            if value:
                return str(value)
    return None


def to_float(value: Any) -> float | None:
    """Безопасно приводит значение к ``float``."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_datetime(raw: Any) -> datetime | None:
    """Парсит дату/время из разных форматов API (ISO, unix, date)."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.isdigit():
            ts = int(text)
            if ts > 1e12:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if " " in text and "T" not in text:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
