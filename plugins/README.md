# Плагины

Скопируйте сюда файл или папку плагина — правки кода бота не нужны.

## Один файл

`my_service.py`:

```python
PLUGIN_NAME = "my_service"  # опционально; по умолчанию — имя файла

from balance_bot.models import ServiceStatus
from balance_bot.plugins.base import ServicePlugin


class Plugin(ServicePlugin):
    async def fetch_status(self) -> ServiceStatus:
        ...
```

В `config.yaml`:

```yaml
services:
  - name: my-account
    plugin: my_service
    ...
```

## Пакет (несколько файлов)

```
plugins/
  my_service/
    __init__.py   # class Plugin, PLUGIN_NAME
    api.py
```

`PLUGIN_NAME` по умолчанию — имя папки.

## Контракт

- Класс `Plugin(ServicePlugin)` с методом `async def fetch_status() -> ServiceStatus`
- `subscription_end` — дата от API сервиса, бот её не вычисляет
- Секреты и параметры — в `plugin_config` сервиса в конфиге

После добавления файла перезапустите бот (`docker compose restart balance-bot`).
