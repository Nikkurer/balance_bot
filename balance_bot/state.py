"""In-memory хранилище последних снимков состояния и активных алертов."""

from balance_bot.models import ServiceStatus


class StateStore:
    """Хранит последний ``ServiceStatus`` и набор активных алертов по сервисам.

    Данные не персистятся на диск и сбрасываются при перезапуске процесса.
    """

    def __init__(self) -> None:
        """Создаёт пустое хранилище."""
        self._status: dict[str, ServiceStatus] = {}
        self._active_alerts: dict[str, set[str]] = {}

    def get_status(self, service_name: str) -> ServiceStatus | None:
        """Возвращает последний снимок состояния сервиса.

        Args:
            service_name: Имя сервиса из конфига.

        Returns:
            Снимок или ``None``, если опрос ещё не выполнялся.
        """
        return self._status.get(service_name)

    def all_statuses(self) -> dict[str, ServiceStatus]:
        """Возвращает копию словаря всех известных состояний.

        Returns:
            Имя сервиса → последний ``ServiceStatus``.
        """
        return dict(self._status)

    def set_status(self, service_name: str, status: ServiceStatus) -> None:
        """Сохраняет снимок после очередного опроса.

        Args:
            service_name: Имя сервиса из конфига.
            status: Результат последнего опроса.
        """
        self._status[service_name] = status

    def get_active_alerts(self, service_name: str) -> set[str]:
        """Возвращает множество активных типов алертов для сервиса.

        Args:
            service_name: Имя сервиса из конфига.

        Returns:
            Подмножество ``{"low_balance", "subscription_ending", "error"}``.
        """
        return self._active_alerts.setdefault(service_name, set())

    def set_active_alerts(self, service_name: str, alerts: set[str]) -> None:
        """Обновляет набор активных алертов после оценки снимка.

        Args:
            service_name: Имя сервиса из конфига.
            alerts: Текущие активные типы алертов.
        """
        self._active_alerts[service_name] = alerts
