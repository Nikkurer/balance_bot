"""Плагин Cloud.ru Evolution: баланс договора через organization API и IAM."""

import logging
import time
from datetime import date, datetime, timedelta, timezone

import aiohttp

from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin

logger = logging.getLogger(__name__)

PLUGIN_NAME = "cloud"

IAM_TOKEN_URL = "https://iam.api.cloud.ru/api/v1/auth/token"
BASE_URL_ORG = "https://organization.api.cloud.ru"

_BALANCE_FIELDS = frozenset({"balance", "money", "real", "bonus", "total"})
_FORECAST_KEYS = (
    "subscription_end",
    "subscriptionEnd",
    "balance_run_out_date",
    "balanceRunOutDate",
    "run_out_date",
    "runOutDate",
    "deactivation_date",
    "deactivationDate",
    "shutdown_at",
    "shutdownAt",
    "expires_at",
    "expiresAt",
    "forecast",
    "paid_until",
    "paidUntil",
)
_DAYS_LEFT_KEYS = (
    "days_left",
    "daysLeft",
    "days_enough",
    "daysEnough",
    "enough_days",
    "enoughDays",
    "balance_days_left",
    "balanceDaysLeft",
)


class CloudApiError(Exception):
    """Ошибка IAM, organization API или разбора ответа Cloud.ru."""


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

        Returns:
            ``ServiceStatus`` с полями баланса/подписки или ``error``.
        """
        cfg = self.service.plugin_config
        agreement_id = str(cfg.get("agreement_id") or "").strip()
        if not agreement_id:
            return ServiceStatus(error="plugin_config.agreement_id обязателен")

        base_url = str(cfg.get("base_url", BASE_URL_ORG)).rstrip("/")
        currency_override = cfg.get("currency")
        balance_field = str(cfg.get("balance_field", "balance")).lower()
        if balance_field not in _BALANCE_FIELDS:
            return ServiceStatus(
                error=(
                    "balance_field должен быть одним из: "
                    f"{', '.join(sorted(_BALANCE_FIELDS))}"
                )
            )

        now = datetime.now(timezone.utc)

        try:
            token = await self._resolve_token(cfg)
            balance_payload = await self._fetch_balance_payload(
                base_url, token, cfg, agreement_id
            )
        except CloudApiError as exc:
            return ServiceStatus(error=str(exc), last_updated=now)
        except aiohttp.ClientError as exc:
            logger.exception("Cloud.ru HTTP error for %s", self.service.name)
            return ServiceStatus(error=f"сеть/API: {exc}", last_updated=now)

        balance = _pick_balance(balance_payload, balance_field)
        subscription_end = _find_forecast(balance_payload)
        if subscription_end is None:
            subscription_end = _forecast_from_days_left(balance_payload, now)

        details = {
            "base_url": base_url,
            "agreement_id": agreement_id,
            "balance_field": balance_field,
            "auth": _resolve_auth_mode(cfg),
        }
        if customer_id := cfg.get("customer_id"):
            details["customer_id"] = customer_id
        if subscription_end is not None:
            details["forecast_source"] = "api"

        return ServiceStatus(
            balance=balance,
            currency=str(currency_override) if currency_override else _find_currency(balance_payload),
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
            return self._cached_token

        key_id = cfg.get("key_id")
        key_secret = cfg.get("key_secret")
        if not key_id or not key_secret:
            raise CloudApiError(
                "plugin_config.key_id и key_secret обязательны (или укажите auth: bearer / api_key)"
            )

        iam_url = str(cfg.get("iam_url", IAM_TOKEN_URL)).strip()
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

    async def _fetch_balance_payload(
        self, base_url: str, token: str, cfg: dict, agreement_id: str
    ) -> dict:
        """Перебирает типовые пути balance API до первого успешного ответа.

        Args:
            base_url: organization API base.
            token: Токен авторизации.
            cfg: ``plugin_config`` (``balance_path``, ``customer_id``, …).
            agreement_id: UUID договора.

        Returns:
            JSON с полями баланса.

        Raises:
            CloudApiError: Ни один candidate path не подошёл.
        """
        auth = _resolve_auth_mode(cfg)
        if custom := cfg.get("balance_path"):
            path = str(custom).format(
                agreement_id=agreement_id,
                customer_id=cfg.get("customer_id", ""),
            )
            return await self._get_json(base_url, path, token, cfg, auth)

        customer_id = str(cfg.get("customer_id") or "").strip()
        candidates = [
            f"/v1/agreements/{agreement_id}/balance",
            f"/v1/agreements/{agreement_id}",
            f"/v1/balance?agreement_id={agreement_id}",
        ]
        if customer_id:
            candidates.extend(
                [
                    f"/v1/customers/{customer_id}/agreements/{agreement_id}/balance",
                    f"/v1/customers/{customer_id}/balance?agreement_id={agreement_id}",
                ]
            )

        last_error: str | None = None
        for path in candidates:
            try:
                payload = await self._get_json(base_url, path, token, cfg, auth)
                if _looks_like_balance_payload(payload):
                    return payload
            except CloudApiError as exc:
                last_error = str(exc)
                logger.debug(
                    "Cloud.ru: %s не подошёл для %s: %s",
                    path,
                    self.service.name,
                    exc,
                )

        if last_error:
            raise CloudApiError(
                f"не удалось получить баланс договора (последняя ошибка: {last_error}). "
                "Укажите balance_path в plugin_config, если ваш endpoint отличается"
            )
        raise CloudApiError("не удалось получить баланс договора")

    async def _get_json(
        self, base_url: str, path: str, token: str, cfg: dict, auth: str
    ) -> dict:
        """GET к organization API.

        Args:
            base_url: Базовый URL.
            path: Относительный или абсолютный путь.
            token: Токен.
            cfg: Конфиг (для совместимости сигнатуры).
            auth: Режим ``key`` / ``bearer`` / ``api_key``.

        Returns:
            JSON-объект ответа.
        """
        url = path if path.startswith("http") else f"{base_url}/{path.lstrip('/')}"
        return await self._request_json("GET", url, cfg, auth_header=_auth_header(token, auth))

    async def _request_json(
        self,
        method: str,
        url: str,
        cfg: dict | None,
        *,
        json_body: dict | None = None,
        auth_header: dict[str, str] | None,
    ) -> dict:
        """Универсальный HTTP-запрос с разбором JSON (IAM и organization API).

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

        async with self._http.request(method, url, headers=headers, json=json_body) as resp:
            try:
                payload = await resp.json(content_type=None)
            except Exception as exc:
                text = await resp.text()
                raise CloudApiError(
                    f"ответ не JSON (HTTP {resp.status}): {text[:200]}"
                ) from exc

            if resp.status >= 400:
                msg = _api_message(payload) or resp.reason or str(resp.status)
                raise CloudApiError(f"HTTP {resp.status} — {msg}")

            if not isinstance(payload, dict):
                raise CloudApiError("неожиданный формат ответа")

            return payload


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


def _looks_like_balance_payload(payload: dict) -> bool:
    """Эвристика: похож ли JSON на ответ с балансом договора.

    Args:
        payload: Тело ответа candidate path.

    Returns:
        ``True``, если есть типовые ключи или вложенный баланс.
    """
    if any(k in payload for k in ("balance", "money", "bonus", "agreement", "data")):
        return True
    return _find_balance_value(payload) is not None or _find_days_left(payload) is not None


def _pick_balance(data: dict, field: str) -> float | None:
    """Извлекает значение баланса по имени поля с обходом вложенных структур.

    Args:
        data: Фрагмент JSON ответа.
        field: ``balance_field`` из конфига.

    Returns:
        Числовой баланс или ``None``.
    """
    if field == "total":
        parts = [
            _pick_balance(data, "real"),
            _pick_balance(data, "bonus"),
            _pick_balance(data, "money"),
            _pick_balance(data, "balance"),
        ]
        present = [p for p in parts if p is not None]
        return sum(present) if present else None

    for key in (field, "balance", "money", "amount", "value"):
        if key in data:
            val = _to_float(data.get(key))
            if val is not None:
                return val

    nested = data.get("agreement") or data.get("balance") or data.get("data")
    if isinstance(nested, dict):
        return _pick_balance(nested, field)
    if isinstance(nested, list) and nested:
        first = nested[0]
        if isinstance(first, dict):
            return _pick_balance(first, field)

    return _find_balance_value(data)


def _find_balance_value(obj: dict, depth: int = 0) -> float | None:
    """Рекурсивный поиск числового баланса в JSON.

    Args:
        obj: Вложенный объект.
        depth: Глубина (лимит 5).

    Returns:
        Первое найденное число или ``None``.
    """
    if depth > 5 or not isinstance(obj, dict):
        return None
    for key in ("balance", "money", "amount", "available", "value"):
        if key in obj:
            val = _to_float(obj[key])
            if val is not None:
                return val
    for value in obj.values():
        if isinstance(value, dict):
            found = _find_balance_value(value, depth + 1)
            if found is not None:
                return found
    return None


def _find_currency(obj: dict, depth: int = 0) -> str | None:
    """Рекурсивный поиск кода валюты в JSON.

    Args:
        obj: Вложенный объект.
        depth: Глубина (лимит 5).

    Returns:
        Строка валюты или ``None``.
    """
    if depth > 5 or not isinstance(obj, dict):
        return None
    for key in ("currency", "currency_code", "currencyCode"):
        if key in obj and obj[key]:
            return str(obj[key])
    for value in obj.values():
        if isinstance(value, dict):
            found = _find_currency(value, depth + 1)
            if found:
                return found
    return None


def _find_forecast(obj: dict, depth: int = 0) -> datetime | None:
    """Рекурсивный поиск даты окончания средств по известным ключам.

    Args:
        obj: Вложенный объект ответа.
        depth: Глубина (лимит 6).

    Returns:
        Дата в UTC или ``None``.
    """
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


def _find_days_left(obj: dict, depth: int = 0) -> int | None:
    """Рекурсивный поиск поля «дней хватит баланса».

    Args:
        obj: Вложенный объект.
        depth: Глубина (лимит 6).

    Returns:
        Целое число дней или ``None``.
    """
    if depth > 6 or not isinstance(obj, dict):
        return None
    for key in _DAYS_LEFT_KEYS:
        if key in obj:
            try:
                return int(float(obj[key]))
            except (TypeError, ValueError):
                continue
    for value in obj.values():
        if isinstance(value, dict):
            found = _find_days_left(value, depth + 1)
            if found is not None:
                return found
    return None


def _forecast_from_days_left(obj: dict, now: datetime) -> datetime | None:
    """Вычисляет дату окончания как ``now + days_left``.

    Args:
        obj: JSON ответа с полем days_left.
        now: Текущее время UTC.

    Returns:
        Прогнозная дата или ``None``.
    """
    days = _find_days_left(obj)
    if days is None or days < 0:
        return None
    return now + timedelta(days=days)


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
