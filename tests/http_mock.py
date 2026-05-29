"""Мок aiohttp.ClientSession для тестов HTTP-плагинов."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch


@dataclass(frozen=True)
class HttpRoute:
    """Описание одного HTTP-ответа для мок-сессии."""

    method: str
    url: str
    status: int
    json_body: Any = None
    headers: dict[str, str] | None = None
    reason: str = ""


class FakeAiohttpResponse:
    """Контекстный менеджер ответа, совместимый с ``async with session.get()``."""

    def __init__(
        self,
        status: int,
        json_body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        reason: str = "Internal Server Error",
    ) -> None:
        self.status = status
        self._json_body = json_body
        self.headers = headers or {}
        self.reason = reason or ""

    async def json(self, content_type: str | None = None) -> Any:
        if self._json_body is None:
            raise aiohttp_content_error()
        return self._json_body

    async def text(self) -> str:
        if self._json_body is None:
            return ""
        if isinstance(self._json_body, str):
            return self._json_body
        import json

        return json.dumps(self._json_body)

    async def __aenter__(self) -> FakeAiohttpResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def aiohttp_content_error() -> Exception:
    """Исключение при попытке разобрать не-JSON тело."""
    import json

    return json.JSONDecodeError("Expecting value", "", 0)


class FakeClientSession:
    """Подмена ``aiohttp.ClientSession`` с маршрутизацией по URL."""

    def __init__(self, routes: list[HttpRoute]) -> None:
        self.closed = False
        self._routes = routes

    def _lookup(self, method: str, url: str) -> FakeAiohttpResponse:
        url_str = str(url)
        for route in self._routes:
            if route.method.upper() != method.upper():
                continue
            if url_str == route.url or url_str.rstrip("/") == route.url.rstrip("/"):
                return FakeAiohttpResponse(
                    route.status,
                    route.json_body,
                    headers=route.headers,
                    reason=route.reason,
                )
        raise AssertionError(f"Unexpected HTTP {method} {url_str!r}")

    def get(self, url: str, **kwargs: Any) -> FakeAiohttpResponse:
        return self._lookup("GET", url)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeAiohttpResponse:
        return self._lookup(method, url)


def patch_aiohttp_session(module: str, routes: list[HttpRoute]):
    """Патчит ``aiohttp.ClientSession`` в модуле плагина."""
    session = FakeClientSession(routes)
    return patch(f"{module}.aiohttp.ClientSession", return_value=session)
