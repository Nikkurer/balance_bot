"""Плагин Cloud.ru Evolution: баланс и гранты договора через BFF console API."""

import json
import logging
import time
from datetime import date, datetime, timezone
from urllib.parse import urlencode

import aiohttp

from balance_bot.http_errors import format_http_error_body
from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin

logger = logging.getLogger(__name__)

PLUGIN_NAME = "cloud"

IAM_TOKEN_URL = "https://iam.api.cloud.ru/api/v1/auth/token"
BASE_URL_BFF = "https://console.cloud.ru/u-api/bff-console"

GRANT_STATUS_READY = "BONUS_GRANT_STATUS_READY"
GRANT_QUERY_STATUSES = (
    GRANT_STATUS_READY,
    "BONUS_GRANT_STATUS_NOT_STARTED",
)


class CloudApiError(Exception):
    """Ошибка IAM, BFF API или разбора ответа Cloud.ru."""


class Plugin(ServicePlugin):
    """Опрашивает баланс договора (``agreement_id``) с кэшем IAM-токена."""

    def __init__(self, service) -> None:
        """Создаёт плагин с HTTP-сессией и кэшем токена.

        Args:
            service: Конфигурация с ``key_id``/``key_secret`` или bearer/api_key.
        """
        super().__init__(service)
        self._http: aiohttp.ClientSession | None = None
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0

    async def fetch_status(self) -> ServiceStatus:
        """Получает баланс и дату окончания средств по договору.

        При наличии активного гранта (``BONUS_GRANT_STATUS_READY``) возвращает
        сумму ``current_amount`` и ближайший ``expire_at``. Иначе — поле
        ``balance`` из BFF; дата окончания неизвестна (в боте «--»).

        Returns:
            ``ServiceStatus`` с полями баланса/подписки или ``error``.
        """
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

            balance = _to_float(balance_payload.get("balance"))
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
        if self._http and not self._http.closed:
            await self._http.close()
        self._http = None

    async def _resolve_token(self, cfg: dict) -> str:
        """Возвращает bearer/api_key или получает IAM access_token по key_id/secret.

        Args:
            cfg: ``plugin_config`` сервиса.

        Returns:
            Строка токена для заголовка Authorization.

        Raises:
            CloudApiError: Нет учётных данных или пустой ответ IAM.
        """
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
        """GET списка грантов договора с фильтром по статусам.

        Args:
            base_url: BFF console API base.
            token: Токен авторизации.
            cfg: ``plugin_config``.
            agreement_id: UUID договора.

        Returns:
            JSON с полем ``bonus_grants``.
        """
        auth = _resolve_auth_mode(cfg)
        query = urlencode([("statuses", status) for status in GRANT_QUERY_STATUSES])
        path = f"/v1/agreements/{agreement_id}/grants?{query}"
        return await self._get_json(base_url, path, token, cfg, auth)

    async def _fetch_balance(
        self, base_url: str, token: str, cfg: dict, agreement_id: str
    ) -> dict:
        """GET баланса договора из BFF v2.

        Args:
            base_url: BFF console API base.
            token: Токен авторизации.
            cfg: ``plugin_config``.
            agreement_id: UUID договора.

        Returns:
            JSON с полем ``balance`` (рубли).
        """
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
        """GET к BFF console API.

        Args:
            base_url: Базовый URL.
            path: Относительный путь (может содержать query).
            token: Токен.
            cfg: Конфиг (для совместимости сигнатуры).
            auth: Режим ``key`` / ``bearer`` / ``api_key``.

        Returns:
            JSON-объект ответа.
        """
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
        """Универсальный HTTP-запрос с разбором JSON (IAM и BFF API).

        Args:
            method: HTTP-метод.
            url: Полный URL.
            cfg: Конфиг (не используется напрямую, для расширений).
            json_body: Тело POST (IAM token).
            auth_header: Заголовки авторизации.

        Returns:
            Распарсенный объект.

        Raises:
            CloudApiError: HTTP ≥400 или не JSON.
        """
        headers = {"Accept": "application/json"}
        if auth_header:
            headers.update(auth_header)
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

        logger.debug(
            "Cloud: %s %s service=%s has_body=%s",
            method,
            url,
            self.service.name,
            json_body is not None,
        )
        async with self._http.request(
            method, url, headers=headers, json=json_body
        ) as resp:
            logger.debug("Cloud: %s %s -> HTTP %s", method, url, resp.status)
            if resp.status >= 400:
                text = await resp.text()
                msg = format_http_error_body(
                    resp.status,
                    text,
                    content_type=resp.headers.get("Content-Type"),
                    reason=resp.reason,
                )
                trace_id = resp.headers.get("x-trace-id")
                request_id = resp.headers.get("x-request-id")
                try:
                    payload = json.loads(text)
                    if isinstance(payload, dict):
                        msg = _api_message(payload) or msg
                        trace_id = _extract_trace_id(payload) or trace_id
                except json.JSONDecodeError:
                    pass
                suffix_parts = []
                if trace_id:
                    suffix_parts.append(f"traceId={trace_id}")
                if request_id:
                    suffix_parts.append(f"requestId={request_id}")
                suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
                logger.error(
                    "Cloud: HTTP %s %s %s — %s%s (service=%s)",
                    resp.status,
                    method,
                    url,
                    msg,
                    suffix,
                    self.service.name,
                )
                raise CloudApiError(f"{url}: HTTP {resp.status} — {msg}{suffix}")

            try:
                payload = await resp.json(content_type=None)
            except Exception as exc:
                raise CloudApiError(
                    f"{url}: ответ не JSON (HTTP {resp.status})"
                ) from exc

            if not isinstance(payload, dict):
                raise CloudApiError(f"{url}: неожиданный формат ответа")

            return payload


def _pick_active_grants(payload: dict) -> list[dict]:
    """Возвращает гранты со статусом READY из ответа BFF.

    Args:
        payload: JSON с ``bonus_grants``.

    Returns:
        Список активных грантов.
    """
    grants = payload.get("bonus_grants")
    if not isinstance(grants, list):
        return []
    return [
        grant
        for grant in grants
        if isinstance(grant, dict) and grant.get("status") == GRANT_STATUS_READY
    ]


def _aggregate_grants(grants: list[dict]) -> tuple[float | None, datetime | None]:
    """Суммирует ``current_amount`` и выбирает ближайший ``expire_at``.

    Args:
        grants: Активные гранты.

    Returns:
        Пара (баланс, дата окончания).
    """
    amounts: list[float] = []
    expires: list[datetime] = []
    for grant in grants:
        amount = _to_float(grant.get("current_amount"))
        if amount is not None:
            amounts.append(amount)
        expire_at = _parse_datetime(grant.get("expire_at"))
        if expire_at is not None:
            expires.append(expire_at)
    balance = sum(amounts) if amounts else None
    subscription_end = min(expires) if expires else None
    return balance, subscription_end


def _resolve_auth_mode(cfg: dict) -> str:
    """Определяет режим auth: ``key`` (IAM), ``bearer`` или ``api_key``.

    Args:
        cfg: ``plugin_config``.

    Returns:
        Одна из строк ``key``, ``bearer``, ``api_key``.

    Raises:
        CloudApiError: Недопустимое значение ``auth``.
    """
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
    """Формирует заголовок Authorization для Cloud.ru API.

    Args:
        token: Секрет или access token.
        auth: Режим авторизации.

    Returns:
        Словарь с ключом ``Authorization``.
    """
    token = token.strip()
    if auth == "api_key":
        return {"Authorization": f"Api-Key {token}"}
    return {"Authorization": f"Bearer {token}"}


def _extract_trace_id(payload: dict | None) -> str | None:
    """Извлекает traceId/requestId из ответа Cloud.ru API."""
    if not isinstance(payload, dict):
        return None
    for key in ("traceId", "trace_id", "requestId", "request_id", "correlationId"):
        value = payload.get(key)
        if value:
            return str(value)
    err = payload.get("error")
    if isinstance(err, dict):
        for key in ("traceId", "trace_id", "requestId", "request_id", "correlationId"):
            value = err.get(key)
            if value:
                return str(value)
    return None


def _api_message(payload: dict | None) -> str | None:
    """Извлекает текст ошибки из JSON Cloud.ru.

    Args:
        payload: Тело ответа.

    Returns:
        Сообщение или ``None``.
    """
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict):
        return err.get("message") or err.get("detail") or err.get("code")
    if isinstance(err, str):
        return err
    return payload.get("message") or payload.get("detail") or payload.get("status_msg")


def _to_float(value) -> float | None:
    """Безопасно приводит значение к ``float``.

    Args:
        value: Значение из JSON.

    Returns:
        Число или ``None``.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(raw) -> datetime | None:
    """Парсит дату/время из ответа Cloud.ru API.

    Args:
        raw: ISO-строка, unix timestamp, ``date`` или ``datetime``.

    Returns:
        ``datetime`` в UTC или ``None``.
    """
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
