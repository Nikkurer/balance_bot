"""HTTP-тесты плагинов aeza, vdsina, cloud с моком aiohttp."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from plugins.aeza import AezaApiError, Plugin as AezaPlugin
from plugins.cloud import CloudApiError, Plugin as CloudPlugin
from plugins.vdsina import VdsinaApiError, Plugin as VdsinaPlugin
from tests.http_mock import HttpRoute, patch_aiohttp_session

AEZA_NET_DESKTOP = "https://core.aeza.net/api/desktop"
AEZA_RU_DESKTOP = "https://my.aeza.ru/api/desktop"
AEZA_RU_ACCOUNTS = "https://my.aeza.ru/api/accounts"
VDSINA_RU_BALANCE = "https://userapi.vdsina.ru/v1/account.balance"
VDSINA_RU_ACCOUNT = "https://userapi.vdsina.ru/v1/account"
CLOUD_BALANCE = (
    "https://organization.api.cloud.ru/v1/agreements/agr-111/balance"
)


@pytest.fixture
def aeza_plugin(make_service):
    service = make_service(
        plugin="aeza",
        plugin_config={
            "api_token": "test-token",
            "site": "net",
            "use_services_forecast": False,
        },
    )
    return AezaPlugin(service)


@pytest.fixture
def aeza_ru_plugin(make_service):
    service = make_service(
        plugin="aeza",
        plugin_config={
            "api_token": "test-token",
            "site": "ru",
            "use_services_forecast": False,
        },
    )
    return AezaPlugin(service)


@pytest.fixture
def vdsina_plugin(make_service):
    service = make_service(
        plugin="vdsina",
        plugin_config={
            "api_token": "test-token",
            "site": "ru",
            "currency": "RUB",
        },
    )
    return VdsinaPlugin(service)


@pytest.fixture
def cloud_plugin(make_service):
    service = make_service(
        plugin="cloud",
        plugin_config={
            "agreement_id": "agr-111",
            "auth": "bearer",
            "access_token": "cloud-token",
            "balance_path": "/v1/agreements/{agreement_id}/balance",
            "base_url": "https://organization.api.cloud.ru",
            "currency": "RUB",
        },
    )
    return CloudPlugin(service)


# --- Aeza ---


@pytest.mark.asyncio
async def test_aeza_get_http_500_includes_trace_id(aeza_ru_plugin: AezaPlugin) -> None:
    routes = [
        HttpRoute(
            "GET",
            AEZA_RU_ACCOUNTS,
            500,
            json_body={
                "error": {
                    "message": "Proxy internal server error",
                    "traceId": "aeza-trace-42",
                }
            },
            headers={"x-request-id": "req-aeza-1"},
        )
    ]
    with patch_aiohttp_session("plugins.aeza", routes):
        with pytest.raises(AezaApiError) as exc_info:
            await aeza_ru_plugin._get(
                AEZA_RU_ACCOUNTS,
                "test-token",
                "api_key",
                params={"current": "1", "extra": "1"},
            )

    msg = str(exc_info.value)
    assert "HTTP 500" in msg
    assert AEZA_RU_ACCOUNTS in msg
    assert "Proxy internal server error" in msg
    assert "traceId=aeza-trace-42" in msg
    assert "requestId=req-aeza-1" in msg


@pytest.mark.asyncio
async def test_aeza_fetch_status_success(aeza_plugin: AezaPlugin) -> None:
    routes = [
        HttpRoute(
            "GET",
            AEZA_NET_DESKTOP,
            200,
            json_body={
                "data": {
                    "balance": {"value": 99.5, "currency": "EUR"},
                    "paidUntil": "2026-06-15T00:00:00+00:00",
                }
            },
        )
    ]
    with patch_aiohttp_session("plugins.aeza", routes):
        status = await aeza_plugin.fetch_status()

    assert status.error is None
    assert status.balance == 99.5
    assert status.currency == "EUR"
    assert status.subscription_end == datetime(
        2026, 6, 15, 0, 0, tzinfo=timezone.utc
    )
    assert status.details["base_url"] == "https://core.aeza.net/api"
    assert status.details["auth"] == "bearer"


@pytest.mark.asyncio
async def test_aeza_ru_fetch_status_success_via_desktop(aeza_ru_plugin: AezaPlugin) -> None:
    routes = [
        HttpRoute(
            "GET",
            AEZA_RU_DESKTOP,
            200,
            json_body={
                "data": {
                    "balance": {"value": 42.0, "currency": "RUB"},
                    "paidUntil": "2026-07-01T00:00:00+00:00",
                }
            },
        )
    ]
    with patch_aiohttp_session("plugins.aeza", routes):
        status = await aeza_ru_plugin.fetch_status()

    assert status.error is None
    assert status.balance == 42.0
    assert status.currency == "RUB"
    assert status.details["auth"] == "api_key"
    assert status.details.get("forecast_source") == "desktop"


@pytest.mark.asyncio
async def test_aeza_fetch_status_maps_http_500_to_error(aeza_ru_plugin: AezaPlugin) -> None:
    routes = [
        HttpRoute(
            "GET",
            AEZA_RU_DESKTOP,
            500,
            json_body={
                "error": {
                    "message": "Proxy internal server error",
                    "traceId": "aeza-trace-42",
                }
            },
        )
    ]
    with patch_aiohttp_session("plugins.aeza", routes):
        status = await aeza_ru_plugin.fetch_status()

    assert status.balance is None
    assert status.error is not None
    assert "HTTP 500" in status.error
    assert "traceId=aeza-trace-42" in status.error


# --- VDSina ---


@pytest.mark.asyncio
async def test_vdsina_get_http_500_includes_request_id(vdsina_plugin: VdsinaPlugin) -> None:
    routes = [
        HttpRoute(
            "GET",
            VDSINA_RU_BALANCE,
            500,
            json_body={"status_msg": "Internal error"},
            headers={"x-request-id": "vds-req-99"},
        )
    ]
    with patch_aiohttp_session("plugins.vdsina", routes):
        with pytest.raises(VdsinaApiError) as exc_info:
            await vdsina_plugin._get(
                "https://userapi.vdsina.ru/v1",
                "test-token",
                "account.balance",
            )

    msg = str(exc_info.value)
    assert "HTTP 500" in msg
    assert VDSINA_RU_BALANCE in msg
    assert "Internal error" in msg
    assert "requestId=vds-req-99" in msg


@pytest.mark.asyncio
async def test_vdsina_fetch_status_success(vdsina_plugin: VdsinaPlugin) -> None:
    routes = [
        HttpRoute(
            "GET",
            VDSINA_RU_BALANCE,
            200,
            json_body={
                "status": "ok",
                "data": {"real": 150.0, "bonus": 10.0, "partner": 0.0},
            },
        ),
        HttpRoute(
            "GET",
            VDSINA_RU_ACCOUNT,
            200,
            json_body={
                "status": "ok",
                "data": {
                    "forecast": "2026-09-01 14:30:00",
                    "account": {"id": 7, "name": "main"},
                },
            },
        ),
    ]
    with patch_aiohttp_session("plugins.vdsina", routes):
        status = await vdsina_plugin.fetch_status()

    assert status.error is None
    assert status.balance == 150.0
    assert status.currency == "RUB"
    assert status.subscription_end == datetime(
        2026, 9, 1, 14, 30, tzinfo=timezone.utc
    )
    assert status.details["account_id"] == 7


@pytest.mark.asyncio
async def test_vdsina_fetch_status_maps_http_500_to_error(vdsina_plugin: VdsinaPlugin) -> None:
    routes = [
        HttpRoute(
            "GET",
            VDSINA_RU_BALANCE,
            500,
            json_body={"status_msg": "upstream failed"},
            headers={"x-request-id": "vds-req-99"},
        )
    ]
    with patch_aiohttp_session("plugins.vdsina", routes):
        status = await vdsina_plugin.fetch_status()

    assert status.error is not None
    assert "HTTP 500" in status.error
    assert "requestId=vds-req-99" in status.error


# --- Cloud.ru ---


@pytest.mark.asyncio
async def test_cloud_request_json_http_500_includes_trace_id(
    cloud_plugin: CloudPlugin,
) -> None:
    routes = [
        HttpRoute(
            "GET",
            CLOUD_BALANCE,
            500,
            json_body={
                "error": {
                    "message": "Internal server error",
                    "traceId": "cloud-trace-7",
                }
            },
            headers={"x-request-id": "cloud-req-3"},
        )
    ]
    with patch_aiohttp_session("plugins.cloud", routes):
        with pytest.raises(CloudApiError) as exc_info:
            await cloud_plugin._request_json(
                "GET",
                CLOUD_BALANCE,
                None,
                auth_header={"Authorization": "Bearer cloud-token"},
            )

    msg = str(exc_info.value)
    assert "HTTP 500" in msg
    assert CLOUD_BALANCE in msg
    assert "Internal server error" in msg
    assert "traceId=cloud-trace-7" in msg
    assert "requestId=cloud-req-3" in msg


@pytest.mark.asyncio
async def test_cloud_fetch_status_success(cloud_plugin: CloudPlugin) -> None:
    routes = [
        HttpRoute(
            "GET",
            CLOUD_BALANCE,
            200,
            json_body={
                "balance": 2500.75,
                "currency": "RUB",
                "balance_run_out_date": "2026-10-20T12:00:00+00:00",
            },
        )
    ]
    with patch_aiohttp_session("plugins.cloud", routes):
        status = await cloud_plugin.fetch_status()

    assert status.error is None
    assert status.balance == 2500.75
    assert status.currency == "RUB"
    assert status.subscription_end == datetime(
        2026, 10, 20, 12, 0, tzinfo=timezone.utc
    )
    assert status.details["agreement_id"] == "agr-111"


@pytest.mark.asyncio
async def test_cloud_fetch_status_maps_http_500_to_error(cloud_plugin: CloudPlugin) -> None:
    routes = [
        HttpRoute(
            "GET",
            CLOUD_BALANCE,
            500,
            json_body={
                "message": "Service unavailable",
                "correlationId": "corr-cloud-1",
            },
        )
    ]
    with patch_aiohttp_session("plugins.cloud", routes):
        status = await cloud_plugin.fetch_status()

    assert status.error is not None
    assert "HTTP 500" in status.error
    assert "traceId=corr-cloud-1" in status.error