"""Тесты SQLite-истории баланса и retention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from balance_bot.history import HistoryStore, _batch_size, resolve_history_path
from balance_bot.models import HistoryConfig, ServiceStatus


@pytest.fixture
def history_db(tmp_path: Path) -> Path:
    return tmp_path / "history.db"


@pytest.fixture
async def history_store(history_db: Path):
    config = HistoryConfig(
        enabled=True,
        path=str(history_db),
        retention_days=30,
        max_size_mb=0,
    )
    store = HistoryStore(config, history_db)
    await store.open()
    yield store
    await store.close()


def test_resolve_history_path_relative_to_config(tmp_path: Path) -> None:
    config_path = tmp_path / "cfg" / "config.yaml"
    resolved = resolve_history_path("data/history.db", config_path)
    assert resolved == (tmp_path / "cfg" / "data" / "history.db").resolve()


def test_batch_size_one_percent() -> None:
    assert _batch_size(0) == 0
    assert _batch_size(50) == 1
    assert _batch_size(250) == 2
    assert _batch_size(10_000) == 100


@pytest.mark.asyncio
async def test_open_logs_new_file(caplog, history_db: Path) -> None:
    config = HistoryConfig(enabled=True, path=str(history_db), retention_days=7, max_size_mb=1)
    store = HistoryStore(config, history_db)

    with caplog.at_level("DEBUG", logger="balance_bot.history"):
        await store.open()
        await store.close()

    path_str = str(history_db)
    assert f"Используется файл {path_str}" in caplog.text
    assert "Создан новый файл" in caplog.text


@pytest.mark.asyncio
async def test_open_logs_existing_file(caplog, history_db: Path) -> None:
    config = HistoryConfig(enabled=True, path=str(history_db), retention_days=7, max_size_mb=1)
    store = HistoryStore(config, history_db)
    await store.open()
    await store.close()

    with caplog.at_level("DEBUG", logger="balance_bot.history"):
        await store.open()
        await store.close()

    path_str = str(history_db)
    assert f"Используется файл {path_str}" in caplog.text
    assert "Файл был найден и используется" in caplog.text


@pytest.mark.asyncio
async def test_record_successful_poll(history_store: HistoryStore) -> None:
    ts = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    status = ServiceStatus(
        balance=42.5,
        currency="RUB",
        subscription_end=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        last_updated=ts,
        details={"source": "grant"},
    )
    await history_store.record("cloud-main", "cloud", status)

    row = history_store._conn.execute(  # noqa: SLF001
        "SELECT ts, service, balance, currency, source, plugin FROM balance_history"
    ).fetchone()
    assert row == (
        "2026-05-01T12:00:00+00:00",
        "cloud-main",
        42.5,
        "RUB",
        "grant",
        "cloud",
    )


@pytest.mark.asyncio
async def test_record_skips_error_by_default(history_store: HistoryStore) -> None:
    await history_store.record(
        "svc",
        "mock",
        ServiceStatus(error="fail", last_updated=datetime.now(timezone.utc)),
    )
    balance_count = history_store._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM balance_history"
    ).fetchone()[0]
    error_count = history_store._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM poll_errors"
    ).fetchone()[0]
    assert balance_count == 0
    assert error_count == 0


@pytest.mark.asyncio
async def test_record_errors_when_enabled(history_db: Path) -> None:
    config = HistoryConfig(
        enabled=True,
        path=str(history_db),
        retention_days=7,
        max_size_mb=1,
        record_errors=True,
    )
    store = HistoryStore(config, history_db)
    await store.open()
    await store.record(
        "svc",
        "mock",
        ServiceStatus(error="timeout", last_updated=datetime.now(timezone.utc)),
    )
    row = store._conn.execute(  # noqa: SLF001
        "SELECT error, service FROM poll_errors"
    ).fetchone()
    balance_count = store._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM balance_history"
    ).fetchone()[0]
    await store.close()
    assert row[0] == "timeout"
    assert row[1] == "svc"
    assert balance_count == 0


@pytest.mark.asyncio
async def test_prune_retention_days_in_one_percent_batches(
    history_db: Path,
) -> None:
    config = HistoryConfig(
        enabled=True,
        path=str(history_db),
        retention_days=7,
        max_size_mb=0,
    )
    store = HistoryStore(config, history_db)
    await store.open()

    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0)
    for i in range(200):
        store._conn.execute(  # noqa: SLF001
            """
            INSERT INTO balance_history (ts, service, balance, currency)
            VALUES (?, ?, ?, ?)
            """,
            (old_ts.isoformat(), f"svc-{i}", float(i), "RUB"),
        )
    recent_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    store._conn.execute(
        """
        INSERT INTO balance_history (ts, service, balance, currency)
        VALUES (?, ?, ?, ?)
        """,
        (recent_ts, "keep", 1.0, "RUB"),
    )
    store._conn.commit()

    stats = await store.prune()
    remaining = store._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM balance_history"
    ).fetchone()[0]

    await store.close()
    assert stats.deleted_rows == 200
    assert remaining == 1


@pytest.mark.asyncio
async def test_prune_max_size_in_one_percent_batches(history_db: Path) -> None:
    config = HistoryConfig(
        enabled=True,
        path=str(history_db),
        retention_days=0,
        max_size_mb=1,
    )
    store = HistoryStore(config, history_db)
    await store.open()

    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for i in range(500):
        store._conn.execute(  # noqa: SLF001
            """
            INSERT INTO balance_history (ts, service, balance, currency, plugin, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ts, "svc", float(i), "RUB", "mock", "x" * 200),
        )
    store._conn.commit()

    with patch(
        "balance_bot.history._database_size_bytes",
        side_effect=[2 * 1024 * 1024, 100],
    ):
        stats = await store.prune()

    remaining = store._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM balance_history"
    ).fetchone()[0]
    await store.close()

    assert stats.deleted_rows > 0
    assert remaining < 500


@pytest.mark.asyncio
async def test_prune_runs_incremental_vacuum_after_deletes(
    history_db: Path,
) -> None:
    config = HistoryConfig(
        enabled=True,
        path=str(history_db),
        retention_days=1,
        max_size_mb=0,
    )
    store = HistoryStore(config, history_db)
    await store.open()

    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).replace(microsecond=0)
    store._conn.execute(  # noqa: SLF001
        """
        INSERT INTO balance_history (ts, service, balance, currency)
        VALUES (?, ?, ?, ?)
        """,
        (old_ts.isoformat(), "svc", 1.0, "RUB"),
    )
    store._conn.commit()

    with patch.object(store, "_incremental_vacuum_sync", return_value=3) as vacuum:
        stats = await store.prune()

    await store.close()
    assert stats.deleted_rows == 1
    vacuum.assert_called_once()


@pytest.mark.asyncio
async def test_prune_skips_when_retention_and_size_disabled(
    history_store: HistoryStore,
) -> None:
    history_store._config = HistoryConfig(  # noqa: SLF001
        enabled=True,
        path=str(history_store.db_path),
        retention_days=0,
        max_size_mb=0,
    )
    await history_store.record(
        "svc",
        "mock",
        ServiceStatus(balance=1.0, last_updated=datetime.now(timezone.utc)),
    )
    stats = await history_store.prune()
    assert stats.deleted_rows == 0


@pytest.mark.asyncio
async def test_fetch_series_returns_points_in_order(history_store: HistoryStore) -> None:
    ts1 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc)
    await history_store.record(
        "svc",
        "mock",
        ServiceStatus(balance=10.0, currency="RUB", last_updated=ts1),
    )
    await history_store.record(
        "svc",
        "mock",
        ServiceStatus(balance=20.0, currency="RUB", last_updated=ts2),
    )

    since = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    points = await history_store.fetch_series("svc", since=since)

    assert len(points) == 1
    assert points[0].balance == 20.0
    assert points[0].currency == "RUB"


@pytest.mark.asyncio
async def test_fetch_chart_data_combines_points_and_errors(history_db: Path) -> None:
    config = HistoryConfig(
        enabled=True,
        path=str(history_db),
        retention_days=7,
        max_size_mb=0,
        record_errors=True,
        chart_max_points=0,
    )
    store = HistoryStore(config, history_db)
    await store.open()
    ts1 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc)
    await store.record(
        "svc",
        "mock",
        ServiceStatus(balance=10.0, currency="RUB", last_updated=ts1),
    )
    await store.record(
        "svc",
        "mock",
        ServiceStatus(balance=20.0, currency="RUB", last_updated=ts2),
    )
    await store.record("svc", "mock", ServiceStatus(error="timeout", last_updated=ts2))

    data = await store.fetch_chart_data(
        "svc", since=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    )
    await store.close()

    assert len(data.points) == 1
    assert data.points[0].balance == 20.0
    assert data.error_count == 1


@pytest.mark.asyncio
async def test_fetch_chart_data_respects_chart_max_points(history_db: Path) -> None:
    config = HistoryConfig(
        enabled=True,
        path=str(history_db),
        retention_days=7,
        max_size_mb=0,
        chart_max_points=2,
    )
    store = HistoryStore(config, history_db)
    await store.open()
    for i in range(5):
        await store.record(
            "svc",
            "mock",
            ServiceStatus(
                balance=float(i),
                last_updated=datetime(2026, 5, 1, i, 0, tzinfo=timezone.utc),
            ),
        )

    data = await store.fetch_chart_data("svc")
    await store.close()

    assert len(data.points) == 2
    assert [p.balance for p in data.points] == [3.0, 4.0]


@pytest.mark.asyncio
async def test_count_poll_errors(history_db: Path) -> None:
    config = HistoryConfig(
        enabled=True,
        path=str(history_db),
        retention_days=7,
        max_size_mb=0,
        record_errors=True,
    )
    store = HistoryStore(config, history_db)
    await store.open()
    ts = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    await store.record("svc", "mock", ServiceStatus(error="504", last_updated=ts))
    count = await store.count_poll_errors(
        "svc", since=datetime(2026, 4, 1, tzinfo=timezone.utc)
    )
    await store.close()
    assert count == 1


@pytest.mark.asyncio
async def test_migrate_legacy_errors_from_balance_history(history_db: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(history_db)
    conn.executescript(
        """
        CREATE TABLE balance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            service TEXT NOT NULL,
            balance REAL,
            currency TEXT,
            subscription_end TEXT,
            error TEXT,
            source TEXT,
            plugin TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO balance_history (ts, service, balance, error, plugin)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("2026-01-01T00:00:00+00:00", "old-svc", None, "legacy fail", "mock"),
    )
    conn.commit()
    conn.close()

    config = HistoryConfig(enabled=True, path=str(history_db), retention_days=7, max_size_mb=0)
    store = HistoryStore(config, history_db)
    await store.open()

    error_row = store._conn.execute(  # noqa: SLF001
        "SELECT service, error FROM poll_errors"
    ).fetchone()
    balance_count = store._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM balance_history"
    ).fetchone()[0]
    await store.close()

    assert error_row == ("old-svc", "legacy fail")
    assert balance_count == 0


@pytest.mark.asyncio
async def test_alert_persistence_roundtrip(history_store: HistoryStore) -> None:
    await history_store.save_alert_persistence("svc-a", {"error", "low_balance"}, 3)
    await history_store.save_alert_persistence("svc-b", set(), 0)

    active, streaks = await history_store.load_alert_persistence()

    assert active == {"svc-a": {"error", "low_balance"}}
    assert streaks == {"svc-a": 3, "svc-b": 0}


@pytest.mark.asyncio
async def test_alert_persistence_overwrites_service(history_store: HistoryStore) -> None:
    await history_store.save_alert_persistence("svc", {"error"}, 2)
    await history_store.save_alert_persistence("svc", {"low_balance"}, 0)

    active, streaks = await history_store.load_alert_persistence()

    assert active == {"svc": {"low_balance"}}
    assert streaks == {"svc": 0}
