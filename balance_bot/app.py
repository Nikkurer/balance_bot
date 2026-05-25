import argparse
import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot

from balance_bot.bot import create_dispatcher, notify_users
from balance_bot.config import ConfigError, load_config
from balance_bot.plugins.loader import (
    create_plugin,
    init_plugins,
    resolve_plugins_dir,
)
from balance_bot.scheduler import Scheduler
from balance_bot.state import StateStore

logger = logging.getLogger(__name__)


async def run(config_path: Path, plugins_dir_override: Path | None = None) -> None:
    config = load_config(config_path)
    plugins_dir = plugins_dir_override or resolve_plugins_dir(
        Path(config.plugins_dir), config_path
    )
    init_plugins(plugins_dir)
    logger.info("Plugins directory: %s", plugins_dir)

    state = StateStore()
    bot = Bot(token=config.bot_token)

    async def on_notify(text: str) -> None:
        await notify_users(bot, config.allowed_user_ids, text)

    scheduler = Scheduler(state, on_notify)
    for service in config.services:
        plugin = create_plugin(service)
        scheduler.add_poller(service, plugin)

    dp = create_dispatcher(config, state, on_refresh=scheduler.poll_all_now)

    scheduler.start_all()
    await scheduler.poll_all_now()

    try:
        await dp.start_polling(bot)
    finally:
        await scheduler.stop_all()
        await bot.session.close()


def main(argv: list[str] | None = None) -> None:
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

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

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
