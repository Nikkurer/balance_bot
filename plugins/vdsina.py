"""Плагин VDSina Public API: баланс и прогноз окончания средств (forecast)."""

import logging
from datetime import datetime, timezone

import aiohttp

from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin
from plugins.http_client import (
    PluginApiError,
    PluginHttpClient,
    parse_datetime,
    to_float,
)

logger = logging.getLogger(__name__)

PLUGIN_NAME = "vdsina"

BASE_URL_RU = "https://userapi.vdsina.ru/v1"
BASE_URL_COM = "https://userapi.vdsina.com/v1"

_BALANCE_FIELDS = frozenset({"real", "bonus", "partner", "total"})


class VdsinaApiError(PluginApiError):
    """Ошибка ответа или логики VDSina API."""


class Plugin(ServicePlugin):
    """Опрашивает ``account.balance`` и ``account`` на userapi.vdsina.ru/com."""

    def __init__(self, service) -> None:
        """Создаёт плагин с ленивой HTTP-сессией.

        Args:
            service: Конфигурация с ``plugin_config`` (``api_token``, ``site``, …).
        """
        super().__init__(service)
        self._client = PluginHttpClient(
            error_class=VdsinaApiError,
            log_prefix="VDSina",
            service_name=service.name,
        )

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
        subscription_end = parse_datetime(account_data.get("forecast"))

        account = account_data.get("account") or {}
        details = {
            "base_url": base_url,
            "balance_field": balance_field,
            "real": to_float(balance_data.get("real")),
            "bonus": to_float(balance_data.get("bonus")),
            "partner": to_float(balance_data.get("partner")),
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
        """Закрывает HTTP-сессию."""
        await self._client.close()

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
        payload = await self._client.request_json(
            "GET",
            url,
            headers=headers,
            message_extractor=_vdsina_api_message,
        )

        status = payload.get("status")
        if status and status != "ok":
            raise VdsinaApiError(f"{url}: {_vdsina_api_message(payload) or status}")

        return payload


def _vdsina_api_message(payload: dict | None) -> str | None:
    """Текст ошибки VDSina (``status_msg`` / ``description``)."""
    if not isinstance(payload, dict):
        return None
    return payload.get("status_msg") or payload.get("description")


def _resolve_base_url(cfg: dict) -> str:
    """Возвращает базовый URL API по ``site`` или ``base_url`` в конфиге."""
    if raw := cfg.get("base_url"):
        return str(raw).rstrip("/")

    site = str(cfg.get("site", "ru")).lower()
    if site in ("com", "vdsina.com", ".com"):
        return BASE_URL_COM
    return BASE_URL_RU


def _unwrap(payload: dict, label: str) -> dict:
    """Извлекает объект ``data`` из обёртки VDSina API."""
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    raise VdsinaApiError(f"{label}: поле data отсутствует в ответе")


def _pick_balance(data: dict, field: str) -> float | None:
    """Выбирает поле баланса (``real``, ``bonus``, ``partner``, ``total``)."""
    if field == "total":
        parts = [
            to_float(data.get("real")),
            to_float(data.get("bonus")),
            to_float(data.get("partner")),
        ]
        present = [p for p in parts if p is not None]
        return sum(present) if present else None
    return to_float(data.get(field))
