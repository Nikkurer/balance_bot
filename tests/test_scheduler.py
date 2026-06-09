"""Тесты планировщика опроса и доставки алертов."""

from datetime import datetime, timezone

import pytest

from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin
from balance_bot.scheduler import Scheduler, ServicePoller
from balance_bot.state import StateStore


class _StaticPlugin(ServicePlugin):
    def __init__(self, service, status: ServiceStatus) -> None:
        super().__init__(service)
        self._status = status

    async def fetch_status(self) -> ServiceStatus:
        return self._status


@pytest.mark.asyncio
async def test_poll_once_records_history(make_service) -> None:
    from unittest.mock import AsyncMock

    state = StateStore()
    status = ServiceStatus(balance=10.0, last_updated=datetime.now(timezone.utc))
    plugin = _StaticPlugin(make_service(), status)
    history = AsyncMock()
    history.record = AsyncMock()
    history.prune = AsyncMock()
    poller = ServicePoller(
        make_service(),
        plugin,
        state,
        on_notify=lambda _: None,
        history=history,
    )

    await poller.poll_once()

    history.record.assert_awaited_once()
    history.prune.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_once_stores_status(make_service) -> None:
    state = StateStore()
    status = ServiceStatus(balance=10.0, last_updated=datetime.now(timezone.utc))
    plugin = _StaticPlugin(make_service(), status)
    poller = ServicePoller(make_service(), plugin, state, on_notify=lambda _: None)

    result = await poller.poll_once()

    assert result is status
    assert state.get_status("test-service") is status


@pytest.mark.asyncio
async def test_poll_once_notifies_only_on_new_alerts(make_service) -> None:
    state = StateStore()
    service = make_service(name="svc", balance_threshold=100.0)
    low = ServiceStatus(balance=5.0, currency="RUB")
    plugin = _StaticPlugin(service, low)
    messages: list[str] = []

    async def capture(text: str) -> None:
        messages.append(text)

    poller = ServicePoller(service, plugin, state, on_notify=capture)

    await poller.poll_once()
    await poller.poll_once()

    assert len(messages) == 1
    assert "баланс 5 RUB" in messages[0]
    assert state.get_active_alerts("svc") == {"low_balance"}


@pytest.mark.asyncio
async def test_poll_once_notifies_again_when_alert_returns(make_service) -> None:
    state = StateStore()
    service = make_service(name="svc", balance_threshold=100.0)
    messages: list[str] = []

    async def capture(text: str) -> None:
        messages.append(text)

    poller = ServicePoller(
        service,
        _StaticPlugin(service, ServiceStatus(balance=5.0)),
        state,
        on_notify=capture,
    )
    await poller.poll_once()

    poller.plugin = _StaticPlugin(service, ServiceStatus(balance=200.0))
    await poller.poll_once()
    assert len(messages) == 1

    poller.plugin = _StaticPlugin(service, ServiceStatus(balance=5.0))
    await poller.poll_once()
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_poll_once_plugin_exception_sets_error_status(make_service) -> None:
    class _FailingPlugin(ServicePlugin):
        async def fetch_status(self) -> ServiceStatus:
            raise RuntimeError("boom")

    state = StateStore()
    service = make_service()
    messages: list[str] = []

    async def capture(text: str) -> None:
        messages.append(text)

    poller = ServicePoller(
        service,
        _FailingPlugin(service),
        state,
        on_notify=capture,
    )
    await poller.poll_once()

    stored = state.get_status("test-service")
    assert stored is not None
    assert stored.error == "boom"
    assert len(messages) == 1
    assert "boom" in messages[0]


@pytest.mark.asyncio
async def test_scheduler_poll_all_now(make_service) -> None:
    state = StateStore()
    scheduler = Scheduler(state, on_notify=lambda _: None)
    scheduler.add_poller(
        make_service(name="a"),
        _StaticPlugin(make_service(name="a"), ServiceStatus(balance=1.0)),
    )
    scheduler.add_poller(
        make_service(name="b"),
        _StaticPlugin(make_service(name="b"), ServiceStatus(balance=2.0)),
    )

    await scheduler.poll_all_now()

    assert state.get_status("a") is not None
    assert state.get_status("b") is not None
