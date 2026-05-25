from balance_bot.models import ServiceStatus


class StateStore:
    """In-memory хранилище последнего состояния и активных алертов."""

    def __init__(self) -> None:
        self._status: dict[str, ServiceStatus] = {}
        self._active_alerts: dict[str, set[str]] = {}

    def get_status(self, service_name: str) -> ServiceStatus | None:
        return self._status.get(service_name)

    def all_statuses(self) -> dict[str, ServiceStatus]:
        return dict(self._status)

    def set_status(self, service_name: str, status: ServiceStatus) -> None:
        self._status[service_name] = status

    def get_active_alerts(self, service_name: str) -> set[str]:
        return self._active_alerts.setdefault(service_name, set())

    def set_active_alerts(self, service_name: str, alerts: set[str]) -> None:
        self._active_alerts[service_name] = alerts
