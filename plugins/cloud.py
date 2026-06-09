"""Плагин Cloud.ru Evolution: баланс и гранты договора через BFF console API."""

import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import aiohttp

from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin
from balance_bot.plugins.http_client import (
    PluginApiError,
    PluginHttpClient,
    parse_datetime,
    to_float,
)

logger = logging.getLogger(__name__)

PLUGIN_NAME = "cloud"

IAM_TOKEN_URL = "https://iam.api.cloud.ru/api/v1/auth/token"
BASE_URL_BFF = "https://console.cloud.ru/u-api/bff-console"

GRANT_STATUS_READY = "BONUS_GRANT_STATUS_READY"
GRANT_QUERY_STATUSES = (
    GRANT_STATUS_READY,
    "BONUS_GRANT_STATUS_NOT_STARTED",
)


class CloudApiError(PluginApiError):
    """Ошибка IAM, BFF API или разбора ответа Cloud.ru."""


class Plugin(ServicePlugin):
    """Опрашивает баланс договора (``agreement_id``) с кэшем IAM-токена."""

    def __init__(self, service) -> None:
        """Создаёт плагин с HTTP-сессией и кэшем токена."""
        super().__init__(service)
        self._client = PluginHttpClient(
            error_class=CloudApiError,
            log_prefix="Cloud",
            service_name=service.name,
        )
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0

    async def fetch_status(self) -> ServiceStatus:
        """Получает баланс и дату окончания средств по договору."""
        cfg = self.service.plugin_config
        agreement_id = str(cfg.get("agreement_id") or "").strip()
        if not agreement_id:
            return ServiceStatus(error="plugin_config.agreement_id обязателен")

        base_url = str(cfg.get("base_url", BASE_URL_BFF)).rstrip("/")
        currency_override = cfg.get("currency")

        now = datetime.now(timezone.utc)
        logger.debug(
            "Cloud fetch_status: service=%s base_url=%s agreement_id=%s auth=%s",
            self.service.name,
            base_url,
            agreement_id,
            _resolve_auth_mode(cfg),
        )

        try:
            token = await self._resolve_token(cfg)
            grants_payload = await self._fetch_grants(base_url, token, cfg, agreement_id)
        except CloudApiError as exc:
            return ServiceStatus(error=str(exc), last_updated=now)
        except aiohttp.ClientError as exc:
            logger.debug(
                "Cloud.ru HTTP error for %s: %s",
                self.service.name,
                exc,
                exc_info=True,
            )
            return ServiceStatus(error=f"сеть/API: {exc}", last_updated=now)

        active_grants = _pick_active_grants(grants_payload)
        details = {
            "base_url": base_url,
            "agreement_id": agreement_id,
            "auth": _resolve_auth_mode(cfg),
        }

        if active_grants:
            balance, subscription_end = _aggregate_grants(active_grants)
            details["source"] = "grant"
            details["grant_count"] = len(active_grants)
            if subscription_end is not None:
                details["forecast_source"] = "grant_expire_at"
        else:
            try:
                balance_payload = await self._fetch_balance(
                    base_url, token, cfg, agreement_id
                )
            except CloudApiError as exc:
                return ServiceStatus(error=str(exc), last_updated=now)
            except aiohttp.ClientError as exc:
                logger.debug(
                    "Cloud.ru HTTP error for %s: %s",
                    self.service.name,
                    exc,
                    exc_info=True,
                )
                return ServiceStatus(error=f"сеть/API: {exc}", last_updated=now)

            balance = to_float(balance_payload.get("balance"))
            subscription_end = None
            details["source"] = "balance"
            details["subscription_end_display"] = "--"
            if balance_payload.get("is_trial") is not None:
                details["is_trial"] = bool(balance_payload.get("is_trial"))

        resolved_currency = str(currency_override) if currency_override else "RUB"
        logger.debug(
            "Cloud fetch_status ok: service=%s balance=%s currency=%s "
            "subscription_end=%s source=%s",
            self.service.name,
            balance,
            resolved_currency,
            subscription_end,
            details.get("source"),
        )
        return ServiceStatus(
            balance=balance,
            currency=resolved_currency,
            subscription_end=subscription_end,
            last_updated=now,
            details=details,
        )

    async def close(self) -> None:
        """Закрывает HTTP-сессию и сбрасывает кэш IAM-токена."""
        await self._client.close()
        self._cached_token = None
        self._token_expires_at = 0.0

    async def _resolve_token(self, cfg: dict) -> str:
        """Возвращает bearer/api_key или получает IAM access_token по key_id/secret."""
        auth = _resolve_auth_mode(cfg)
        logger.debug("Cloud _resolve_token: service=%s auth=%s", self.service.name, auth)
        if auth == "bearer":
            token = cfg.get("access_token") or cfg.get("api_token")
            if not token or not str(token).strip():
                raise CloudApiError(
                    "plugin_config.access_token (или api_token) обязателен при auth: bearer"
                )
            return str(token).strip()

        if auth == "api_key":
            token = cfg.get("api_token") or cfg.get("api_key")
            if not token or not str(token).strip():
                raise CloudApiError(
                    "plugin_config.api_token (или api_key) обязателен при auth: api_key"
                )
            return str(token).strip()

        if self._cached_token and time.monotonic() < self._token_expires_at:
            logger.debug("Cloud _resolve_token: используем кэш IAM-токена")
            return self._cached_token

        key_id = cfg.get("key_id")
        key_secret = cfg.get("key_secret")
        if not key_id or not key_secret:
            raise CloudApiError(
                "plugin_config.key_id и key_secret обязательны (или укажите auth: bearer / api_key)"
            )

        iam_url = str(cfg.get("iam_url", IAM_TOKEN_URL)).strip()
        logger.debug("Cloud _resolve_token: запрос IAM %s", iam_url)
        payload = await self._request_json(
            "POST",
            iam_url,
            None,
            json_body={"keyId": str(key_id).strip(), "secret": str(key_secret).strip()},
            auth_header=None,
        )
        token = (
            payload.get("access_token")
            or payload.get("accessToken")
            or payload.get("token")
        )
        if not token:
            raise CloudApiError("IAM: в ответе нет access_token")

        expires_in = payload.get("expires_in") or payload.get("expiresIn") or 3600
        try:
            ttl = max(60, int(expires_in) - 60)
        except (TypeError, ValueError):
            ttl = 3540

        self._cached_token = str(token)
        self._token_expires_at = time.monotonic() + ttl
        return self._cached_token

    async def _fetch_grants(
        self, base_url: str, token: str, cfg: dict, agreement_id: str
    ) -> dict:
        """GET списка грантов договора с фильтром по статусам."""
        auth = _resolve_auth_mode(cfg)
        query = urlencode([("statuses", status) for status in GRANT_QUERY_STATUSES])
        path = f"/v1/agreements/{agreement_id}/grants?{query}"
        return await self._get_json(base_url, path, token, cfg, auth)

    async def _fetch_balance(
        self, base_url: str, token: str, cfg: dict, agreement_id: str
    ) -> dict:
        """GET баланса договора из BFF v2."""
        auth = _resolve_auth_mode(cfg)
        path = f"/v2/agreements/{agreement_id}/balance"
        return await self._get_json(base_url, path, token, cfg, auth)

    async def _get_json(
        self,
        base_url: str,
        path: str,
        token: str,
        cfg: dict,
        auth: str,
    ) -> dict:
        """GET к BFF console API."""
        url = path if path.startswith("http") else f"{base_url}/{path.lstrip('/')}"
        return await self._request_json(
            "GET",
            url,
            cfg,
            auth_header=_auth_header(token, auth),
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        cfg: dict | None,
        *,
        json_body: dict | None = None,
        auth_header: dict[str, str] | None,
    ) -> dict:
        """Универсальный HTTP-запрос с разбором JSON (IAM и BFF API)."""
        headers = {"Accept": "application/json"}
        if auth_header:
            headers.update(auth_header)
        return await self._client.request_json(
            method,
            url,
            headers=headers,
            json_body=json_body,
        )


def _pick_active_grants(payload: dict) -> list[dict]:
    """Возвращает гранты со статусом READY из ответа BFF."""
    grants = payload.get("bonus_grants")
    if not isinstance(grants, list):
        return []
    return [
        grant
        for grant in grants
        if isinstance(grant, dict) and grant.get("status") == GRANT_STATUS_READY
    ]


def _aggregate_grants(grants: list[dict]) -> tuple[float | None, datetime | None]:
    """Суммирует ``current_amount`` и выбирает ближайший ``expire_at``."""
    amounts: list[float] = []
    expires: list[datetime] = []
    for grant in grants:
        amount = to_float(grant.get("current_amount"))
        if amount is not None:
            amounts.append(amount)
        expire_at = parse_datetime(grant.get("expire_at"))
        if expire_at is not None:
            expires.append(expire_at)
    balance = sum(amounts) if amounts else None
    subscription_end = min(expires) if expires else None
    return balance, subscription_end


def _resolve_auth_mode(cfg: dict) -> str:
    """Определяет режим auth: ``key`` (IAM), ``bearer`` или ``api_key``."""
    if raw := cfg.get("auth"):
        auth = str(raw).lower()
        if auth in ("key", "bearer", "api_key"):
            return auth
        raise CloudApiError("auth должен быть key, bearer или api_key")
    if cfg.get("access_token"):
        return "bearer"
    if cfg.get("api_key") and not cfg.get("key_id"):
        return "api_key"
    return "key"


def _auth_header(token: str, auth: str) -> dict[str, str]:
    """Формирует заголовок Authorization для Cloud.ru API."""
    token = token.strip()
    if auth == "api_key":
        return {"Authorization": f"Api-Key {token}"}
    return {"Authorization": f"Bearer {token}"}
