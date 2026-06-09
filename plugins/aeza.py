"""Плагин Aeza (aeza.ru / aeza.net): баланс и дата окончания средств."""

import logging
from datetime import datetime, timezone

import aiohttp

from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin
from balance_bot.plugins.http_client import (
    PluginApiError,
    PluginHttpClient,
    extract_trace_id,
    parse_datetime,
    to_float,
)

logger = logging.getLogger(__name__)

PLUGIN_NAME = "aeza"

# .net — backend core.aeza.net (как в aeza-net-sdk)
BASE_URL_NET = "https://core.aeza.net/api"
# .ru — API личного кабинета aeza.ru (по аналогии с официальным my.aeza.net/api)
BASE_URL_RU = "https://my.aeza.ru/api"

_FORECAST_KEYS = (
    "forecast",
    "paidUntil",
    "paid_until",
    "shutdownAt",
    "shutdown_at",
    "expiresAt",
    "expires_at",
    "until",
)


class AezaApiError(PluginApiError):
    """Ошибка ответа или логики Aeza API."""


class Plugin(ServicePlugin):
    """Опрашивает ``GET /desktop`` Aeza (.net Bearer, .ru X-API-Key)."""

    def __init__(self, service) -> None:
        """Создаёт плагин с ленивой HTTP-сессией.

        Args:
            service: Конфигурация с ``plugin_config`` (``api_token``, ``site``, …).
        """
        super().__init__(service)
        self._client = PluginHttpClient(
            error_class=AezaApiError,
            log_prefix="Aeza",
            service_name=service.name,
        )

    async def fetch_status(self) -> ServiceStatus:
        """Запрашивает баланс и ближайшую дату окончания средств.

        Returns:
            ``ServiceStatus`` или снимок с ``error`` при сбое API.
        """
        cfg = self.service.plugin_config
        token = cfg.get("api_token")
        if not token or not str(token).strip():
            return ServiceStatus(error="plugin_config.api_token обязателен")

        base_url = _resolve_base_url(cfg)
        auth = _resolve_auth(cfg, base_url)
        currency_override = cfg.get("currency")
        use_services = bool(cfg.get("use_services_forecast", True))
        now = datetime.now(timezone.utc)
        logger.debug(
            "Aeza fetch_status: service=%s base_url=%s auth=%s use_services=%s",
            self.service.name,
            base_url,
            auth,
            use_services,
        )

        try:
            # /accounts?current=1 на my.aeza.ru периодически отдаёт HTTP 500;
            # /desktop принимает и Bearer, и X-API-Key (проверено curl).
            payload = await self._get(f"{base_url}/desktop", token, auth)
            data = _unwrap_data(payload, "desktop")
            forecast_source = "desktop"
        except AezaApiError as exc:
            return ServiceStatus(error=str(exc), last_updated=now)
        except aiohttp.ClientError as exc:
            logger.debug("Aeza HTTP error for %s: %s", self.service.name, exc)
            return ServiceStatus(error=f"сеть/API: {exc}", last_updated=now)

        balance, currency = _parse_balance(data)
        if currency_override:
            currency = str(currency_override)

        subscription_end = _find_forecast(data)
        if subscription_end is None and use_services:
            try:
                subscription_end = await self._forecast_from_services(
                    base_url, token, auth
                )
                if subscription_end is not None:
                    forecast_source = "services"
            except (AezaApiError, aiohttp.ClientError) as exc:
                logger.debug(
                    "Aeza: не удалось получить дату из services для %s: %s",
                    self.service.name,
                    exc,
                )

        details = {
            "base_url": base_url,
            "auth": auth,
            "balance_raw": data.get("balance"),
        }
        if subscription_end is not None:
            details["forecast_source"] = forecast_source

        logger.debug(
            "Aeza fetch_status ok: service=%s balance=%s currency=%s subscription_end=%s source=%s",
            self.service.name,
            balance,
            currency,
            subscription_end,
            details.get("forecast_source"),
        )
        return ServiceStatus(
            balance=balance,
            currency=currency,
            subscription_end=subscription_end,
            last_updated=now,
            details=details,
        )

    async def close(self) -> None:
        """Закрывает HTTP-сессию."""
        await self._client.close()

    async def _forecast_from_services(
        self, base_url: str, token: str, auth: str
    ) -> datetime | None:
        """Ищет минимальную дату forecast среди услуг ``/services``."""
        payload = await self._get(
            f"{base_url}/services",
            token,
            auth,
            params={"offset": "0", "count": "100", "sort": ""},
        )
        items = _unwrap_data(payload, "services").get("items") or []
        if not isinstance(items, list):
            return None

        dates: list[datetime] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            found = _find_forecast(item)
            if found:
                dates.append(found)
                continue
            timestamps = item.get("timestamps")
            if isinstance(timestamps, dict):
                found = _find_forecast(timestamps)
                if found:
                    dates.append(found)

        if not dates:
            return None
        return min(dates)

    async def _get(
        self,
        url: str,
        token: str,
        auth: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict:
        """Выполняет GET к Aeza API.

        Raises:
            AezaApiError: HTTP ≥400, не JSON или поле ``error`` в теле.
        """
        headers = {"Accept": "application/json", **_auth_headers(token, auth)}
        payload = await self._client.request_json(
            "GET",
            url,
            headers=headers,
            params=params,
        )

        if payload.get("error"):
            err = payload["error"]
            if isinstance(err, dict):
                msg = err.get("message") or err.get("slug") or str(err)
            else:
                msg = str(err)
            trace_id = extract_trace_id(payload)
            suffix = f" (traceId={trace_id})" if trace_id else ""
            raise AezaApiError(f"{url}: {msg}{suffix}")

        return payload


def _resolve_base_url(cfg: dict) -> str:
    """Возвращает ``BASE_URL_RU`` или ``BASE_URL_NET`` по ``site``/``base_url``."""
    if raw := cfg.get("base_url"):
        return str(raw).rstrip("/")

    site = str(cfg.get("site", "net")).lower()
    if site in ("ru", "aeza.ru", ".ru"):
        return BASE_URL_RU
    return BASE_URL_NET


def _resolve_auth(cfg: dict, base_url: str) -> str:
    """Определяет способ авторизации (bearer / api_key)."""
    if raw := cfg.get("auth"):
        auth = str(raw).lower()
        if auth in ("bearer", "api_key"):
            return auth
        raise AezaApiError("auth должен быть bearer или api_key")

    if "my.aeza." in base_url:
        return "api_key"
    return "bearer"


def _auth_headers(token: str, auth: str) -> dict[str, str]:
    """Формирует заголовки Authorization или X-API-Key."""
    token = token.strip()
    if auth == "api_key":
        return {"X-API-Key": token}
    return {"Authorization": f"Bearer {token}"}


def _unwrap_data(payload: dict, label: str) -> dict:
    """Извлекает ``data`` из обёртки ответа Aeza."""
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    raise AezaApiError(f"{label}: поле data отсутствует в ответе")


def _parse_balance(data: dict) -> tuple[float | None, str | None]:
    """Парсит баланс и валюту из объекта аккаунта/desktop."""
    balance_obj = data.get("balance")
    if isinstance(balance_obj, dict):
        value = balance_obj.get("value")
        if value is None:
            value = balance_obj.get("amount")
        currency = balance_obj.get("currency") or balance_obj.get("code")
        return _normalize_balance_value(value, balance_obj), (
            str(currency) if currency else None
        )
    if balance_obj is not None:
        return _normalize_balance_value(balance_obj), None
    return _normalize_balance_value(data.get("balance")), None


def _balance_divisor(balance_obj: dict | None) -> float:
    """Возвращает делитель для перевода ``value`` в основные единицы валюты."""
    if isinstance(balance_obj, dict):
        raw_round = balance_obj.get("round")
        if raw_round is not None:
            try:
                return float(10 ** int(raw_round))
            except (TypeError, ValueError):
                pass
    return 100.0


def _normalize_balance_value(value, balance_obj: dict | None = None) -> float | None:
    """Переводит сумму из копеек/центов в основные единицы валюты."""
    parsed = to_float(value)
    if parsed is None:
        return None
    return parsed / _balance_divisor(balance_obj)


def _find_forecast(obj: dict, depth: int = 0) -> datetime | None:
    """Рекурсивно ищет поле даты окончания средств в JSON."""
    if depth > 6 or not isinstance(obj, dict):
        return None
    for key in _FORECAST_KEYS:
        if key in obj:
            parsed = parse_datetime(obj[key])
            if parsed:
                return parsed
    for value in obj.values():
        if isinstance(value, dict):
            parsed = _find_forecast(value, depth + 1)
            if parsed:
                return parsed
    return None
