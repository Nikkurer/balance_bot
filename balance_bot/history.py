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
    balance          REAL    NOT NULL,
    currency         TEXT,
    subscription_end TEXT,
    source           TEXT,
    plugin           TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_service_ts
    ON balance_history (service, ts DESC);

CREATE INDEX IF NOT EXISTS idx_history_ts
    ON balance_history (ts);

CREATE TABLE IF NOT EXISTS poll_errors (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,
    service TEXT    NOT NULL,
    plugin  TEXT,
    error   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_poll_errors_service_ts
    ON poll_errors (service, ts DESC);

CREATE INDEX IF NOT EXISTS idx_poll_errors_ts
    ON poll_errors (ts);
"""


@dataclass(frozen=True)
class BalancePoint:
    """Точка временного ряда баланса."""

    ts: datetime
    balance: float
    currency: str | None


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


def _format_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat()


def _parse_ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


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

    @property
    def chart_points_per_day(self) -> int:
        """Лимит точек на графике за сутки (``0`` — без усреднения)."""
        return self._config.chart_points_per_day

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

        Успешные опросы — в ``balance_history``; ошибки при ``record_errors`` —
        в ``poll_errors``.

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

    async def fetch_series(
        self,
        service: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[BalancePoint]:
        """Возвращает ряд баланса для сервиса (по возрастанию времени).

        Args:
            service: Имя сервиса.
            since: Нижняя граница ``ts`` (UTC); ``None`` — без ограничения.
            limit: Максимум точек; ``None`` — без ограничения.

        Returns:
            Список точек баланса.
        """
        return await asyncio.to_thread(self._fetch_series_sync, service, since, limit)

    async def count_poll_errors(
        self,
        service: str,
        *,
        since: datetime | None = None,
    ) -> int:
        """Считает записи об ошибках опроса за период.

        Args:
            service: Имя сервиса.
            since: Нижняя граница ``ts`` (UTC); ``None`` — без ограничения.

        Returns:
            Число строк в ``poll_errors``.
        """
        return await asyncio.to_thread(self._count_poll_errors_sync, service, since)

    def _open_sync(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        existed = self._db_path.is_file()
        path_str = str(self._db_path)
        logger.info("Используется файл %s", path_str)
        if existed:
            logger.debug("Файл был найден и используется")
        else:
            logger.debug("Создан новый файл")
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        self._migrate_legacy_errors_sync()

    def _migrate_legacy_errors_sync(self) -> None:
        """Переносит строки с ошибками из старой ``balance_history`` в ``poll_errors``."""
        assert self._conn is not None
        columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(balance_history)")
        }
        if "error" not in columns:
            return
        rows = self._conn.execute(
            """
            SELECT ts, service, plugin, error
            FROM balance_history
            WHERE error IS NOT NULL
            """
        ).fetchall()
        if rows:
            self._conn.executemany(
                """
                INSERT INTO poll_errors (ts, service, plugin, error)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.execute(
                "DELETE FROM balance_history WHERE error IS NOT NULL"
            )
            self._conn.execute(
                "DELETE FROM balance_history WHERE balance IS NULL"
            )
            self._conn.commit()
            logger.info(
                "HistoryStore: мигрировано %d строк ошибок в poll_errors",
                len(rows),
            )

    def _close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("HistoryStore: closed %s", self._db_path)

    def _record_sync(self, service: str, plugin: str, status: ServiceStatus) -> None:
        if self._conn is None:
            raise RuntimeError("HistoryStore не открыт")

        ts = status.last_updated or datetime.now(timezone.utc)
        ts_iso = _format_ts(ts)

        if status.error:
            if not self._config.record_errors:
                return
            self._conn.execute(
                """
                INSERT INTO poll_errors (ts, service, plugin, error)
                VALUES (?, ?, ?, ?)
                """,
                (ts_iso, service, plugin, status.error),
            )
            self._conn.commit()
            logger.debug(
                "HistoryStore: poll_error service=%s error=%s",
                service,
                status.error,
            )
            return

        if status.balance is None:
            return

        subscription_end = (
            _format_ts(status.subscription_end) if status.subscription_end else None
        )
        source = status.details.get("source")
        if source is not None:
            source = str(source)

        self._conn.execute(
            """
            INSERT INTO balance_history (
                ts, service, balance, currency, subscription_end, source, plugin
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts_iso,
                service,
                status.balance,
                status.currency,
                subscription_end,
                source,
                plugin,
            ),
        )
        self._conn.commit()
        logger.debug(
            "HistoryStore: record service=%s balance=%s",
            service,
            status.balance,
        )

    def _fetch_series_sync(
        self,
        service: str,
        since: datetime | None,
        limit: int | None,
    ) -> list[BalancePoint]:
        if self._conn is None:
            raise RuntimeError("HistoryStore не открыт")

        since_iso = _format_ts(since) if since is not None else None
        query = """
            SELECT ts, balance, currency
            FROM balance_history
            WHERE service = ?
        """
        params: list[object] = [service]
        if since_iso is not None:
            query += " AND ts >= ?"
            params.append(since_iso)
        query += " ORDER BY ts ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [
            BalancePoint(ts=_parse_ts(row[0]), balance=float(row[1]), currency=row[2])
            for row in rows
        ]

    def _count_poll_errors_sync(self, service: str, since: datetime | None) -> int:
        if self._conn is None:
            raise RuntimeError("HistoryStore не открыт")

        since_iso = _format_ts(since) if since is not None else None
        query = "SELECT COUNT(*) FROM poll_errors WHERE service = ?"
        params: list[object] = [service]
        if since_iso is not None:
            query += " AND ts >= ?"
            params.append(since_iso)
        row = self._conn.execute(query, params).fetchone()
        return int(row[0]) if row else 0

    def _prune_sync(self) -> PruneStats:
        if self._conn is None:
            raise RuntimeError("HistoryStore не открыт")

        deleted_rows = 0
        while True:
            removed = self._prune_retention_days_batch("balance_history")
            if removed <= 0:
                break
            deleted_rows += removed
        while True:
            removed = self._prune_retention_days_batch("poll_errors")
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

    def _count_rows(self, table: str) -> int:
        assert self._conn is not None
        row = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0

    def _count_all_rows(self) -> int:
        return self._count_rows("balance_history") + self._count_rows("poll_errors")

    def _retention_cutoff_iso(self) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._config.retention_days)
        return _format_ts(cutoff)

    def _prune_retention_days_batch(self, table: str) -> int:
        if self._config.retention_days <= 0:
            return 0
        assert self._conn is not None
        cutoff = self._retention_cutoff_iso()
        expired = self._conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE ts < ?",
            (cutoff,),
        ).fetchone()
        if not expired or int(expired[0]) == 0:
            return 0
        batch = _batch_size(self._count_rows(table))
        cur = self._conn.execute(
            f"""
            DELETE FROM {table} WHERE id IN (
                SELECT id FROM {table}
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
        total = self._count_all_rows()
        batch = _batch_size(total)
        if batch <= 0:
            return 0
        assert self._conn is not None
        removed = self._delete_oldest_batch("balance_history", batch)
        if removed > 0:
            self._conn.commit()
            return removed
        removed = self._delete_oldest_batch("poll_errors", batch)
        if removed > 0:
            self._conn.commit()
        return removed

    def _delete_oldest_batch(self, table: str, batch: int) -> int:
        assert self._conn is not None
        cur = self._conn.execute(
            f"""
            DELETE FROM {table} WHERE id IN (
                SELECT id FROM {table}
                ORDER BY ts ASC, id ASC
                LIMIT ?
            )
            """,
            (batch,),
        )
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
