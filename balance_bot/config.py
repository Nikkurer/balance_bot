from pathlib import Path

import yaml

from balance_bot.models import AppConfig, ServiceConfig


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    telegram = raw["telegram"]
    services = [
        ServiceConfig(
            name=s["name"],
            plugin=s["plugin"],
            poll_interval_seconds=int(s["poll_interval_seconds"]),
            balance_threshold=s.get("balance_threshold"),
            subscription_warn_days=s.get("subscription_warn_days"),
            plugin_config=s.get("plugin_config") or {},
        )
        for s in raw.get("services", [])
    ]

    return AppConfig(
        bot_token=telegram["bot_token"],
        allowed_user_ids=[int(uid) for uid in telegram["allowed_user_ids"]],
        services=services,
        plugins_dir=raw.get("plugins_dir", "plugins"),
    )
