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
        "SELECT ts, service, balance, currency, source, plugin, error FROM balance_history"
    ).fetchone()
    assert row == (
        "2026-05-01T12:00:00+00:00",
        "cloud-main",
        42.5,
        "RUB",
        "grant",
        "cloud",
        None,
    )


@pytest.mark.asyncio
async def test_record_skips_error_by_default(history_store: HistoryStore) -> None:
    await history_store.record(
        "svc",
        "mock",
        ServiceStatus(error="fail", last_updated=datetime.now(timezone.utc)),
    )
    count = history_store._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM balance_history"
    ).fetchone()[0]
    assert count == 0


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
        "SELECT error, balance FROM balance_history"
    ).fetchone()
    await store.close()
    assert row == ("timeout", None)


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
