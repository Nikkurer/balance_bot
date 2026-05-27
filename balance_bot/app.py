"""Точка входа: загрузка конфига, планировщик опроса, long polling Telegram."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from balance_bot.bot import create_dispatcher, notify_users, register_bot_commands
from balance_bot.logging_setup import setup_logging
from balance_bot.config import ConfigError, load_config
from balance_bot.plugins.loader import (
    create_plugin,
    ensure_plugins_for_services,
    init_plugins,
    registered_plugins,
    resolve_plugins_dir,
)
from balance_bot.scheduler import Scheduler
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)


async def run(config_path: Path, plugins_dir_override: Path | None = None) -> None:
    """Запускает бота: плагины, опрос сервисов, Telegram polling.

    Args:
        config_path: Путь к YAML-конфигурации.
        plugins_dir_override: Каталог плагинов; если ``None``, берётся из конфига
            относительно каталога конфига.
    """
    config = load_config(config_path)
    plugins_dir = plugins_dir_override or resolve_plugins_dir(
        Path(config.plugins_dir), config_path
    )
    init_plugins(plugins_dir)
    loaded = registered_plugins()
    logger.info("Каталог плагинов: %s (загружено: %s)", plugins_dir, ", ".join(loaded) or "—")
    ensure_plugins_for_services(config.services)

    state = StateStore()
    # Увеличенный таймаут снижает ложные обрывы long polling в Docker/WSL
    bot = Bot(
        token=config.bot_token,
        session=AiohttpSession(timeout=90),
    )

    async def on_notify(text: str) -> None:
        await notify_users(bot, config.allowed_user_ids, text)

    scheduler = Scheduler(state, on_notify)
    for service in config.services:
        plugin = create_plugin(service)
        scheduler.add_poller(service, plugin)
    logger.info("Запущен мониторинг %d сервис(ов)", len(config.services))

    dp = create_dispatcher(config, state, on_refresh=scheduler.poll_all_now)

    await register_bot_commands(bot)
    logger.info("Команды бота зарегистрированы в Telegram")

    scheduler.start_all()
    await scheduler.poll_all_now()

    try:
        await dp.start_polling(bot)
    finally:
        await scheduler.stop_all()
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
    args = parser.parse_args(argv)

    setup_logging(verbose=args.verbose)

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    plugins_override = Path(args.plugins_dir) if args.plugins_dir else None

    try:
        asyncio.run(run(config_path, plugins_override))
    except ConfigError as exc:
        logger.error("Некорректная конфигурация:\n%s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Stopped")
