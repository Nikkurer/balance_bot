from datetime import datetime, timezone

from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin

PLUGIN_NAME = "mock"


class Plugin(ServicePlugin):
    """Тестовый плагин — данные из plugin_config, без внешних запросов."""

    async def fetch_status(self) -> ServiceStatus:
        cfg = self.service.plugin_config
        subscription_end = None
        if raw_end := cfg.get("subscription_end"):
            subscription_end = datetime.fromisoformat(raw_end)
            if subscription_end.tzinfo is None:
                subscription_end = subscription_end.replace(tzinfo=timezone.utc)

        return ServiceStatus(
            balance=cfg.get("balance"),
            currency=cfg.get("currency", "RUB"),
            subscription_end=subscription_end,
            last_updated=datetime.now(timezone.utc),
            details={"source": "mock"},
        )
