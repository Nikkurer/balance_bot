"""SQLite-хранилище истории баланса с политикой retention."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from balance_bot.models import HistoryConfig, ServiceStatus

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS balance_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT    NOT NULL,
    service          TEXT    NOT NULL,
    balance          REAL,
    currency         TEXT,
    subscription_end TEXT,
    error            TEXT,
    source           TEXT,
    plugin           TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_service_ts
    ON balance_history (service, ts DESC);

CREATE INDEX IF NOT EXISTS idx_history_ts
    ON balance_history (ts);
"""


@dataclass(frozen=True)
class PruneStats:
    """Результат очистки истории."""

    deleted_rows: int
    vacuum_pages: int


def resolve_history_path(path: str, config_path: Path) -> Path:
    """Разрешает путь к файлу БД относительно каталога конфига.

    Args:
        path: Путь из конфига.
        config_path: Путь к ``config.yaml``.

    Returns:
        Абсолютный путь к файлу SQLite.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (config_path.parent / candidate).resolve()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _format_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat()


def _batch_size(total_rows: int) -> int:
    if total_rows <= 0:
        return 0
    return max(1, total_rows // 100)


def _database_size_bytes(db_path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        part = Path(f"{db_path}{suffix}")
        if part.is_file():
            total += part.stat().st_size
    return total


class HistoryStore:
    """Запись и retention истории баланса в SQLite."""

    def __init__(self, config: HistoryConfig, db_path: Path) -> None:
        """Создаёт хранилище (без открытия соединения).

        Args:
            config: Настройки history из конфига.
            db_path: Абсолютный путь к файлу БД.
        """
        self._config = config
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        """Путь к файлу SQLite."""
        return self._db_path

    async def open(self) -> None:
        """Создаёт каталог, схему и открывает соединение."""
        await asyncio.to_thread(self._open_sync)

    async def close(self) -> None:
        """Закрывает соединение с БД."""
        await asyncio.to_thread(self._close_sync)

    async def record(
        self,
        service: str,
        plugin: str,
        status: ServiceStatus,
    ) -> None:
        """Добавляет строку истории после опроса.

        Args:
            service: Имя сервиса из конфига.
            plugin: Имя плагина.
            status: Снимок опроса.
        """
        await asyncio.to_thread(self._record_sync, service, plugin, status)

    async def prune(self) -> PruneStats:
        """Применяет retention и ``incremental_vacuum`` при удалениях.

        Returns:
            Статистика очистки.
        """
        return await asyncio.to_thread(self._prune_sync)

    def _open_sync(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        logger.debug("HistoryStore: opened %s", self._db_path)

    def _close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("HistoryStore: closed %s", self._db_path)

    def _record_sync(self, service: str, plugin: str, status: ServiceStatus) -> None:
        if self._conn is None:
            raise RuntimeError("HistoryStore не открыт")

        if status.error:
            if not self._config.record_errors:
                return
        elif status.balance is None:
            return

        ts = status.last_updated or datetime.now(timezone.utc)
        subscription_end = (
            _format_ts(status.subscription_end) if status.subscription_end else None
        )
        source = status.details.get("source")
        if source is not None:
            source = str(source)

        self._conn.execute(
            """
            INSERT INTO balance_history (
                ts, service, balance, currency, subscription_end, error, source, plugin
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _format_ts(ts),
                service,
                status.balance,
                status.currency,
                subscription_end,
                status.error,
                source,
                plugin,
            ),
        )
        self._conn.commit()
        logger.debug(
            "HistoryStore: record service=%s balance=%s error=%s",
            service,
            status.balance,
            status.error,
        )

    def _prune_sync(self) -> PruneStats:
        if self._conn is None:
            raise RuntimeError("HistoryStore не открыт")

        deleted_rows = 0
        while True:
            removed = self._prune_retention_days_batch()
            if removed <= 0:
                break
            deleted_rows += removed
        while True:
            removed = self._prune_max_size_batch()
            if removed <= 0:
                break
            deleted_rows += removed

        vacuum_pages = 0
        if deleted_rows > 0:
            vacuum_pages = self._incremental_vacuum_sync()
            logger.info(
                "HistoryStore: prune deleted %d row(s), vacuum_pages=%s path=%s",
                deleted_rows,
                vacuum_pages,
                self._db_path,
            )
        return PruneStats(deleted_rows=deleted_rows, vacuum_pages=vacuum_pages)

    def _count_rows(self) -> int:
        assert self._conn is not None
        row = self._conn.execute("SELECT COUNT(*) FROM balance_history").fetchone()
        return int(row[0]) if row else 0

    def _retention_cutoff_iso(self) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._config.retention_days)
        return _format_ts(cutoff)

    def _prune_retention_days_batch(self) -> int:
        if self._config.retention_days <= 0:
            return 0
        assert self._conn is not None
        cutoff = self._retention_cutoff_iso()
        expired = self._conn.execute(
            "SELECT COUNT(*) FROM balance_history WHERE ts < ?",
            (cutoff,),
        ).fetchone()
        if not expired or int(expired[0]) == 0:
            return 0
        batch = _batch_size(self._count_rows())
        cur = self._conn.execute(
            """
            DELETE FROM balance_history WHERE id IN (
                SELECT id FROM balance_history
                WHERE ts < ?
                ORDER BY ts ASC, id ASC
                LIMIT ?
            )
            """,
            (cutoff, batch),
        )
        self._conn.commit()
        return int(cur.rowcount)

    def _prune_max_size_batch(self) -> int:
        if self._config.max_size_mb <= 0:
            return 0
        max_bytes = self._config.max_size_mb * 1024 * 1024
        if _database_size_bytes(self._db_path) <= max_bytes:
            return 0
        batch = _batch_size(self._count_rows())
        if batch <= 0:
            return 0
        assert self._conn is not None
        cur = self._conn.execute(
            """
            DELETE FROM balance_history WHERE id IN (
                SELECT id FROM balance_history
                ORDER BY ts ASC, id ASC
                LIMIT ?
            )
            """,
            (batch,),
        )
        self._conn.commit()
        return int(cur.rowcount)

    def _incremental_vacuum_sync(self) -> int:
        assert self._conn is not None
        pages_before = self._conn.execute("PRAGMA freelist_count").fetchone()
        self._conn.execute("PRAGMA incremental_vacuum")
        self._conn.commit()
        pages_after = self._conn.execute("PRAGMA freelist_count").fetchone()
        before = int(pages_before[0]) if pages_before else 0
        after = int(pages_after[0]) if pages_after else 0
        return max(0, before - after)
