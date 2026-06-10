"""Точка входа: загрузка конфига, планировщик опроса, long polling Telegram."""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from balance_bot.bot import create_dispatcher, notify_users, register_bot_commands
from balance_bot.config import load_config
from balance_bot.exceptions import ConfigError
from balance_bot.logging_setup import setup_logging
from balance_bot.history import HistoryStore, resolve_history_path
from balance_bot.plugins.loader import (
    create_plugin,
    ensure_plugins_for_services,
    init_plugins,
    registered_plugins,
    resolve_plugins_dir,
)
from balance_bot.scheduler import Scheduler
from balance_bot.state import StateStore
from balance_bot.timezone import set_bot_timezone

logger = logging.getLogger(__name__)


def _is_debug_enabled(cli_verbose: bool, cli_debug: bool) -> bool:
    """Определяет, нужно ли включать debug-логи.

    Приоритет: CLI-флаги ``-v``/``--debug`` или переменная ``BALANCE_BOT_DEBUG``.
    """
    if cli_verbose or cli_debug:
        return True
    return os.getenv("BALANCE_BOT_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def run(
    config_path: Path,
    plugins_dir_override: Path | None = None,
    *,
    verbose: bool = False,
) -> None:
    """Запускает бота: плагины, опрос сервисов, Telegram polling.

    Args:
        config_path: Путь к YAML-конфигурации.
        plugins_dir_override: Каталог плагинов; если ``None``, берётся из конфига
            относительно каталога конфига.
        verbose: Флаг подробного логирования.
    """
    config = load_config(config_path)
    set_bot_timezone(config.timezone)
    setup_logging(verbose=verbose, timezone_name=config.timezone)
    logger.debug(
        "Старт run(): config=%s, plugins_override=%s, timezone=%s, debug=%s",
        config_path,
        plugins_dir_override,
        config.timezone,
        verbose,
    )
    plugins_dir = plugins_dir_override or resolve_plugins_dir(
        Path(config.plugins_dir), config_path
    )
    logger.debug("Разрешён каталог плагинов: %s", plugins_dir)
    init_plugins(plugins_dir)
    loaded = registered_plugins()
    logger.info("Каталог плагинов: %s (загружено: %s)", plugins_dir, ", ".join(loaded) or "—")
    logger.debug("Загруженные плагины: %s", loaded)
    ensure_plugins_for_services(config.services)
    logger.debug(
        "Конфиг сервисов: %s",
        [
            {
                "name": s.name,
                "plugin": s.plugin,
                "interval": s.poll_interval_seconds,
            }
            for s in config.services
        ],
    )

    state = StateStore()
    history_store: HistoryStore | None = None
    if config.history.enabled:
        db_path = resolve_history_path(config.history.path, config_path)
        logger.debug("Хранение истории баланса включено: %s", db_path)
        history_store = HistoryStore(config.history, db_path)
        await history_store.open()
        prune_stats = await history_store.prune()
        if prune_stats.deleted_rows > 0:
            logger.info(
                "Начальный prune истории: удалено %d строк, vacuum_pages=%s",
                prune_stats.deleted_rows,
                prune_stats.vacuum_pages,
            )
        logger.debug(
            "History config: retention_days=%s max_size_mb=%s record_errors=%s "
            "prune_interval_hours=%s",
            config.history.retention_days,
            config.history.max_size_mb,
            config.history.record_errors,
            config.history.prune_interval_hours,
        )

    # Увеличенный таймаут снижает ложные обрывы long polling в Docker/WSL
    bot = Bot(
        token=config.bot_token,
        session=AiohttpSession(timeout=90),
    )

    async def on_notify(text: str) -> None:
        logger.debug(
            "on_notify(): отправка уведомления %d пользователям",
            len(config.allowed_user_ids),
        )
        await notify_users(bot, config.allowed_user_ids, text)

    prune_interval = (
        config.history.prune_interval_hours if config.history.enabled else 0
    )
    scheduler = Scheduler(
        state,
        on_notify,
        history=history_store,
        prune_interval_hours=prune_interval,
        alerts_config=config.alerts,
    )
    if history_store is not None and config.alerts.persist:
        active_alerts, error_streaks = await history_store.load_alert_persistence()
        state.hydrate_alerts(active_alerts)
        scheduler.hydrate_error_streaks(error_streaks)
        logger.debug(
            "Восстановлено состояние алертов: %d сервис(ов), streaks=%s",
            len(active_alerts),
            error_streaks,
        )
    for service in config.services:
        plugin = create_plugin(service)
        scheduler.add_poller(service, plugin)
        logger.debug(
            "Создан poller: service=%s plugin=%s class=%s",
            service.name,
            service.plugin,
            plugin.__class__.__name__,
        )
    logger.info("Запущен мониторинг %d сервис(ов)", len(config.services))

    dp = create_dispatcher(
        config,
        state,
        on_refresh=scheduler.poll_all_now,
        history=history_store,
    )

    await register_bot_commands(bot)
    logger.info("Команды бота зарегистрированы в Telegram")
    logger.debug("Команды бота зарегистрированы для текущего scope/языков")

    await scheduler.poll_all_now(suppress_alerts=config.alerts.suppress_on_startup)
    logger.debug(
        "Первичный poll_all_now() завершён (suppress_on_startup=%s)",
        config.alerts.suppress_on_startup,
    )
    scheduler.start_all(delay_first=True)
    logger.debug("Фоновые poller'ы запущены (первый tick после interval)")

    try:
        logger.debug("Запуск Telegram long polling")
        await dp.start_polling(bot)
    finally:
        logger.debug("Остановка планировщика и закрытие bot session")
        await scheduler.stop_all()
        if history_store is not None:
            await history_store.close()
        await bot.session.close()


def main(argv: list[str] | None = None) -> None:
    """CLI: разбор аргументов, настройка логов, ``asyncio.run(run)``.

    Args:
        argv: Аргументы командной строки; ``None`` — ``sys.argv``.

    Raises:
        SystemExit: Код 1 при отсутствии конфига или ``ConfigError``.
    """
    parser = argparse.ArgumentParser(description="Balance bot — мониторинг балансов и подписок")
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="Путь к файлу конфигурации (по умолчанию: config.yaml)",
    )
    parser.add_argument(
        "--plugins-dir",
        help="Каталог плагинов (переопределяет plugins_dir из конфига)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Подробные логи")
    parser.add_argument("--debug", action="store_true", help="Синоним verbose-режима")
    args = parser.parse_args(argv)
    debug_enabled = _is_debug_enabled(args.verbose, args.debug)
    logger.debug(
        "CLI args parsed: config=%s plugins_dir=%s verbose=%s debug=%s -> enabled=%s",
        args.config,
        args.plugins_dir,
        args.verbose,
        args.debug,
        debug_enabled,
    )

    config_path = Path(args.config)
    if not config_path.exists():
        setup_logging(verbose=debug_enabled)
        logging.getLogger(__name__).error("Config file not found: %s", config_path)
        sys.exit(1)

    plugins_override = Path(args.plugins_dir) if args.plugins_dir else None

    try:
        asyncio.run(run(config_path, plugins_override, verbose=debug_enabled))
    except ConfigError as exc:
        logger.error("Некорректная конфигурация:\n%s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Stopped")
