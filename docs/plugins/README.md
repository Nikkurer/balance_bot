# Плагины

Код плагинов — в каталоге [`plugins/`](../plugins/) в корне репозитория. Скопируйте туда файл или папку — правки кода бота не нужны.

## Trust boundary (граница доверия)

Каталог `plugins_dir` — **полностью доверенный** код:

- При старте бот добавляет каталог в `sys.path` и импортирует каждый модуль; это обычный Python с доступом к `balance_bot`, сети и переменным окружения.
- Секреты из `plugin_config` и `bot_token` доступны процессу бота; вредоносный плагин может их прочитать или отправить наружу.
- В Docker каталог монтируется как `./plugins:/plugins:ro` — запись в volume запрещена, но **выполнение кода не ограничено**.

Добавляйте только свои или аудированные плагины. Для недоверенных источников нужна отдельная изоляция (отдельный контейнер, другой пользователь ОС) — вне scope этого проекта.

## Документация по плагинам

| Плагин | Файл | Описание |
|--------|------|----------|
| mock | [mock.md](mock.md) | Тест без внешнего API |
| vdsina | [vdsina.md](vdsina.md) | VDSina (.ru / .com) |
| aeza | [aeza.md](aeza.md) | Aeza (.ru / .net) |
| cloud | [cloud.md](cloud.md) | Cloud.ru Evolution |

Новый плагин: скопируйте [PLUGIN_TEMPLATE.md](PLUGIN_TEMPLATE.md) в этот каталог как `<имя>.md` и заполните.

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

После добавления файла перезапустите бот (`docker compose restart balance-bot` или с `-f docker-compose.dev.yml`).
