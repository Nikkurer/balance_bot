import logging
from datetime import date, datetime, timezone

import aiohttp

from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin

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


class AezaApiError(Exception):
    pass


class Plugin(ServicePlugin):
    """Плагин Aeza (aeza.ru / aeza.net)."""

    def __init__(self, service) -> None:
        super().__init__(service)
        self._http: aiohttp.ClientSession | None = None

    async def fetch_status(self) -> ServiceStatus:
        cfg = self.service.plugin_config
        token = cfg.get("api_token")
        if not token or not str(token).strip():
            return ServiceStatus(error="plugin_config.api_token обязателен")

        base_url = _resolve_base_url(cfg)
        auth = _resolve_auth(cfg, base_url)
        currency_override = cfg.get("currency")
        use_services = bool(cfg.get("use_services_forecast", True))
        now = datetime.now(timezone.utc)

        try:
            if auth == "bearer":
                payload = await self._get(f"{base_url}/desktop", token, auth)
                data = _unwrap_data(payload, "desktop")
                forecast_source = "desktop"
            else:
                payload = await self._get(
                    f"{base_url}/accounts",
                    token,
                    auth,
                    params={"current": "1", "extra": "1"},
                )
                data = _unwrap_account(payload)
                forecast_source = "accounts"
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

        return ServiceStatus(
            balance=balance,
            currency=currency,
            subscription_end=subscription_end,
            last_updated=now,
            details=details,
        )

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()
        self._http = None

    async def _forecast_from_services(
        self, base_url: str, token: str, auth: str
    ) -> datetime | None:
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
        headers = {"Accept": "application/json", **_auth_headers(token, auth)}
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

        async with self._http.get(url, headers=headers, params=params) as resp:
            try:
                payload = await resp.json(content_type=None)
            except Exception as exc:
                text = await resp.text()
                raise AezaApiError(
                    f"ответ не JSON (HTTP {resp.status}): {text[:200]}"
                ) from exc

            if resp.status >= 400:
                msg = _api_message(payload) or resp.reason
                raise AezaApiError(f"HTTP {resp.status} — {msg}")

            if not isinstance(payload, dict):
                raise AezaApiError("неожиданный формат ответа")

            if payload.get("error"):
                err = payload["error"]
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("slug") or str(err)
                else:
                    msg = str(err)
                raise AezaApiError(msg)

            return payload


def _resolve_base_url(cfg: dict) -> str:
    if raw := cfg.get("base_url"):
        return str(raw).rstrip("/")

    site = str(cfg.get("site", "net")).lower()
    if site in ("ru", "aeza.ru", ".ru"):
        return BASE_URL_RU
    return BASE_URL_NET


def _resolve_auth(cfg: dict, base_url: str) -> str:
    if raw := cfg.get("auth"):
        auth = str(raw).lower()
        if auth in ("bearer", "api_key"):
            return auth
        raise AezaApiError("auth должен быть bearer или api_key")

    if "my.aeza." in base_url:
        return "api_key"
    return "bearer"


def _auth_headers(token: str, auth: str) -> dict[str, str]:
    token = token.strip()
    if auth == "api_key":
        return {"X-API-Key": token}
    return {"Authorization": f"Bearer {token}"}


def _unwrap_data(payload: dict, label: str) -> dict:
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    raise AezaApiError(f"{label}: поле data отсутствует в ответе")


def _unwrap_account(payload: dict) -> dict:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise AezaApiError("accounts: поле data отсутствует в ответе")

    items = data.get("items")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            return first
    if isinstance(data, dict) and ("balance" in data or "id" in data):
        return data
    raise AezaApiError("accounts: не найден текущий аккаунт")


def _api_message(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict):
        return err.get("message") or err.get("slug")
    return payload.get("status_msg") or payload.get("message")


def _parse_balance(data: dict) -> tuple[float | None, str | None]:
    balance_obj = data.get("balance")
    if isinstance(balance_obj, dict):
        value = balance_obj.get("value")
        if value is None:
            value = balance_obj.get("amount")
        currency = balance_obj.get("currency") or balance_obj.get("code")
        return _to_float(value), str(currency) if currency else None
    if balance_obj is not None:
        return _to_float(balance_obj), None
    return _to_float(data.get("balance")), None


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_forecast(obj: dict, depth: int = 0) -> datetime | None:
    if depth > 6 or not isinstance(obj, dict):
        return None
    for key in _FORECAST_KEYS:
        if key in obj:
            parsed = _parse_datetime(obj[key])
            if parsed:
                return parsed
    for value in obj.values():
        if isinstance(value, dict):
            parsed = _find_forecast(value, depth + 1)
            if parsed:
                return parsed
    return None


def _parse_datetime(raw) -> datetime | None:
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
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
