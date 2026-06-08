"""Плагин VDSina Public API: баланс и прогноз окончания средств (forecast)."""

import json
import logging
from datetime import date, datetime, timezone

import aiohttp

from balance_bot.http_errors import format_http_error_body
from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin

logger = logging.getLogger(__name__)

PLUGIN_NAME = "vdsina"

BASE_URL_RU = "https://userapi.vdsina.ru/v1"
BASE_URL_COM = "https://userapi.vdsina.com/v1"

_BALANCE_FIELDS = frozenset({"real", "bonus", "partner", "total"})


class VdsinaApiError(Exception):
    """Ошибка ответа или логики VDSina API."""


class Plugin(ServicePlugin):
    """Опрашивает ``account.balance`` и ``account`` на userapi.vdsina.ru/com."""

    def __init__(self, service) -> None:
        """Создаёт плагин с ленивой HTTP-сессией.

        Args:
            service: Конфигурация с ``plugin_config`` (``api_token``, ``site``, …).
        """
        super().__init__(service)
        self._http: aiohttp.ClientSession | None = None

    async def fetch_status(self) -> ServiceStatus:
        """Запрашивает баланс и дату forecast из VDSina API.

        Returns:
            ``ServiceStatus`` с балансом и ``subscription_end`` или ``error``.
        """
        cfg = self.service.plugin_config
        token = cfg.get("api_token")
        if not token or not str(token).strip():
            return ServiceStatus(error="plugin_config.api_token обязателен")

        base_url = _resolve_base_url(cfg)
        balance_field = str(cfg.get("balance_field", "real")).lower()
        if balance_field not in _BALANCE_FIELDS:
            return ServiceStatus(
                error=f"balance_field должен быть одним из: {', '.join(sorted(_BALANCE_FIELDS))}"
            )

        currency = cfg.get("currency")
        now = datetime.now(timezone.utc)
        logger.debug(
            "VDSina fetch_status: service=%s base_url=%s balance_field=%s",
            self.service.name,
            base_url,
            balance_field,
        )

        try:
            balance_payload = await self._get(base_url, token, "account.balance")
            account_payload = await self._get(base_url, token, "account")
        except VdsinaApiError as exc:
            return ServiceStatus(error=str(exc), last_updated=now)
        except aiohttp.ClientError as exc:
            logger.debug("VDSina HTTP error for %s: %s", self.service.name, exc, exc_info=True)
            return ServiceStatus(error=f"сеть/API: {exc}", last_updated=now)

        balance_data = _unwrap(balance_payload, "account.balance")
        account_data = _unwrap(account_payload, "account")

        balance = _pick_balance(balance_data, balance_field)
        subscription_end = _parse_forecast(account_data.get("forecast"))

        account = account_data.get("account") or {}
        details = {
            "base_url": base_url,
            "balance_field": balance_field,
            "real": _to_float(balance_data.get("real")),
            "bonus": _to_float(balance_data.get("bonus")),
            "partner": _to_float(balance_data.get("partner")),
            "account_id": account.get("id"),
            "account_name": account.get("name"),
        }

        logger.debug(
            "VDSina fetch_status ok: service=%s balance=%s currency=%s subscription_end=%s",
            self.service.name,
            balance,
            currency,
            subscription_end,
        )
        return ServiceStatus(
            balance=balance,
            currency=str(currency) if currency else None,
            subscription_end=subscription_end,
            last_updated=now,
            details=details,
        )

    async def close(self) -> None:
        """Закрывает ``aiohttp.ClientSession``."""
        if self._http and not self._http.closed:
            await self._http.close()
        self._http = None

    async def _get(self, base_url: str, token: str, path: str) -> dict:
        """Выполняет GET к VDSina API и проверяет ``status: ok``.

        Args:
            base_url: Базовый URL (ru или com).
            token: Bearer-токен из ``plugin_config.api_token``.
            path: Путь ресурса, например ``account.balance``.

        Returns:
            Распарсенный JSON-объект ответа.

        Raises:
            VdsinaApiError: HTTP ≥400, не JSON или ``status != ok``.
        """
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {token.strip()}",
            "Accept": "application/json",
        }
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

        logger.debug(
            "VDSina: GET %s service=%s",
            url,
            self.service.name,
        )
        async with self._http.get(url, headers=headers) as resp:
            logger.debug("VDSina: GET %s -> HTTP %s", url, resp.status)
            if resp.status >= 400:
                text = await resp.text()
                msg = format_http_error_body(
                    resp.status,
                    text,
                    content_type=resp.headers.get("Content-Type"),
                    reason=resp.reason,
                )
                try:
                    payload = json.loads(text)
                    if isinstance(payload, dict):
                        msg = _api_message(payload) or msg
                except json.JSONDecodeError:
                    pass
                request_id = resp.headers.get("x-request-id")
                suffix = f" (requestId={request_id})" if request_id else ""
                raise VdsinaApiError(f"{url}: HTTP {resp.status} — {msg}{suffix}")

            try:
                payload = await resp.json(content_type=None)
            except Exception as exc:
                raise VdsinaApiError(
                    f"{url}: ответ не JSON (HTTP {resp.status})"
                ) from exc

            if not isinstance(payload, dict):
                raise VdsinaApiError(f"{url}: неожиданный формат ответа")

            status = payload.get("status")
            if status and status != "ok":
                raise VdsinaApiError(f"{url}: {_api_message(payload) or status}")

            return payload


def _resolve_base_url(cfg: dict) -> str:
    """Возвращает базовый URL API по ``site`` или ``base_url`` в конфиге.

    Args:
        cfg: ``plugin_config`` сервиса.

    Returns:
        ``BASE_URL_RU`` или ``BASE_URL_COM``.
    """
    if raw := cfg.get("base_url"):
        return str(raw).rstrip("/")

    site = str(cfg.get("site", "ru")).lower()
    if site in ("com", "vdsina.com", ".com"):
        return BASE_URL_COM
    return BASE_URL_RU


def _unwrap(payload: dict, label: str) -> dict:
    """Извлекает объект ``data`` из обёртки VDSina API.

    Args:
        payload: Тело ответа API.
        label: Имя ресурса для сообщения об ошибке.

    Returns:
        Словарь ``data``.

    Raises:
        VdsinaApiError: Поле ``data`` отсутствует или не объект.
    """
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    raise VdsinaApiError(f"{label}: поле data отсутствует в ответе")


def _api_message(payload: dict | None) -> str | None:
    """Достаёт текст ошибки из JSON ответа VDSina.

    Args:
        payload: Тело ответа или ``None``.

    Returns:
        Сообщение или ``None``.
    """
    if not isinstance(payload, dict):
        return None
    return payload.get("status_msg") or payload.get("description")


def _to_float(value) -> float | None:
    """Безопасно приводит значение к ``float``.

    Args:
        value: Число или строка из API.

    Returns:
        Число или ``None`` при ошибке преобразования.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_balance(data: dict, field: str) -> float | None:
    """Выбирает поле баланса (``real``, ``bonus``, ``partner``, ``total``).

    Args:
        data: Объект ``account.balance`` из API.
        field: Имя поля из ``plugin_config.balance_field``.

    Returns:
        Сумма или значение поля.
    """
    if field == "total":
        parts = [
            _to_float(data.get("real")),
            _to_float(data.get("bonus")),
            _to_float(data.get("partner")),
        ]
        present = [p for p in parts if p is not None]
        return sum(present) if present else None
    return _to_float(data.get(field))


def _parse_forecast(raw) -> datetime | None:
    """Парсит дату прогноза окончания средств из ответа ``account``.

    Args:
        raw: Строка ISO, ``date``, ``datetime`` или пустое значение.

    Returns:
        Дата в UTC или ``None``.
    """
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    try:
        if " " in text:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
