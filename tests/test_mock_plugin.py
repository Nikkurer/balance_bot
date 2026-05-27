"""Тесты mock-плагина."""

from datetime import datetime, timezone

import pytest

from balance_bot.plugins.loader import create_plugin, init_plugins
from tests.conftest import PLUGINS_DIR


@pytest.fixture
def mock_plugin(make_service):
    init_plugins(PLUGINS_DIR)
    service = make_service(
        plugin="mock",
        plugin_config={
            "balance": 77.0,
            "currency": "USD",
            "subscription_end": "2026-12-31T00:00:00+00:00",
        },
    )
    return create_plugin(service)


@pytest.mark.asyncio
async def test_fetch_status_from_config(mock_plugin) -> None:
    status = await mock_plugin.fetch_status()

    assert status.error is None
    assert status.balance == 77.0
    assert status.currency == "USD"
    assert status.subscription_end == datetime(2026, 12, 31, tzinfo=timezone.utc)
    assert status.last_updated is not None
    assert status.details.get("source") == "mock"


@pytest.mark.asyncio
async def test_fetch_status_naive_subscription_end(mock_plugin, make_service) -> None:
    init_plugins(PLUGINS_DIR)
    service = make_service(
        plugin="mock",
        plugin_config={"subscription_end": "2026-01-15T00:00:00"},
    )
    plugin = create_plugin(service)
    status = await plugin.fetch_status()

    assert status.subscription_end is not None
    assert status.subscription_end.tzinfo == timezone.utc
