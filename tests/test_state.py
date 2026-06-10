"""Тесты in-memory хранилища состояния."""

from balance_bot.models import ServiceStatus
from balance_bot.state import StateStore


def test_set_and_get_status() -> None:
    store = StateStore()
    status = ServiceStatus(balance=100.0, currency="RUB")
    store.set_status("a", status)
    assert store.get_status("a") is status
    assert store.get_status("missing") is None


def test_all_statuses_returns_copy() -> None:
    store = StateStore()
    store.set_status("a", ServiceStatus(balance=1.0))
    snapshot = store.all_statuses()
    snapshot["b"] = ServiceStatus(balance=2.0)
    assert store.get_status("b") is None
    assert len(store.all_statuses()) == 1


def test_active_alerts_default_empty() -> None:
    store = StateStore()
    assert store.get_active_alerts("svc") == set()


def test_set_active_alerts() -> None:
    store = StateStore()
    store.set_active_alerts("svc", {"low_balance"})
    assert store.get_active_alerts("svc") == {"low_balance"}
    store.set_active_alerts("svc", set())
    assert store.get_active_alerts("svc") == set()


def test_hydrate_alerts() -> None:
    store = StateStore()
    store.hydrate_alerts({"a": {"error"}, "b": {"low_balance", "subscription_ending"}})
    assert store.get_active_alerts("a") == {"error"}
    assert store.get_active_alerts("b") == {"low_balance", "subscription_ending"}
