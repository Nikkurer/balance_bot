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

## VDSina (`vdsina.py`)

### Как получить API-токен

Аккаунты **vdsina.ru** и **vdsina.com** разные — для каждого сайта нужен **свой** токен.

#### vdsina.ru

1. Войдите в [панель управления](https://cp.vdsina.ru/) (или [my.vdsina.ru](https://my.vdsina.ru/)).
2. Откройте раздел аккаунта → **API**: [my.vdsina.ru/account/api](https://my.vdsina.ru/account/api).
3. Скопируйте постоянный токен API (или создайте/обновите, если панель предлагает).
4. Вставьте в `config.yaml` → `plugin_config.api_token` для сервиса с `site: ru`.

#### vdsina.com

1. Войдите в [панель vdsina.com](https://cp.vdsina.com/).
2. В настройках аккаунта найдите раздел **API** (аналогично `.ru`).
3. Скопируйте токен для **этого** аккаунта.
4. Укажите в конфиге для сервиса с `site: com` (отдельная запись в `services`).

#### Важно

- Токен привязан к пользователю панели: права API = права этого пользователя. Для ограниченного доступа создайте отдельного пользователя в аккаунте и возьмите токен от него.
- При **смене пароля** пользователя токен, как правило, **сбрасывается** — нужно скопировать новый и обновить `config.yaml`.
- Токен не коммитьте в git; храните только в локальном `config.yaml` (он в `.gitignore`).

### Пример конфигурации

```yaml
- name: vdsina-ru
  plugin: vdsina
  poll_interval_seconds: 3600
  balance_threshold: 500
  subscription_warn_days: 7
  plugin_config:
    api_token: "..."
    site: ru          # ru → userapi.vdsina.ru, com → userapi.vdsina.com
    currency: RUB
    balance_field: real   # real | bonus | partner | total
```

- **balance** — из `GET /account.balance` (`balance_field`, по умолчанию `real`)
- **subscription_end** — `forecast` из `GET /account` (дата отключения от API)
- Опционально: `base_url` вместо `site` для явного URL API

## Aeza (`aeza.py`)

Аккаунты **aeza.ru** и **aeza.net** разные — нужны **разные токены** и параметр `site`.

### Как выбирается API (`site`)

| `site` | URL по умолчанию | Авторизация по умолчанию | Эндпоинт баланса |
|--------|------------------|--------------------------|------------------|
| `net` (по умолчанию) | `https://core.aeza.net/api` | `Bearer` | `GET /desktop` |
| `ru` | `https://my.aeza.ru/api` | `X-API-Key` | `GET /accounts?current=1` |

Переопределение: `base_url` и/или `auth: bearer | api_key` в `plugin_config`.

### Как получить API-токен

**aeza.net**

1. [my.aeza.net](https://my.aeza.net/) → **Настройки** → [API-ключи](https://my.aeza.net/settings/apikeys).
2. Создайте ключ, укажите в конфиге с `site: net`.

**aeza.ru**

1. [my.aeza.ru](https://my.aeza.ru/) (или панель aeza.ru) → настройки → API-ключи.
2. Токен в конфиге с `site: ru`.

Токены не взаимозаменяемы между `.ru` и `.net`.

### Пример конфигурации

```yaml
- name: aeza-net
  plugin: aeza
  plugin_config:
    api_token: "..."
    site: net
    currency: RUB

- name: aeza-ru
  plugin: aeza
  plugin_config:
    api_token: "..."
    site: ru
    currency: RUB
```

- **balance** — из desktop (`net` + Bearer) или accounts (`ru` + API-ключ)
- **subscription_end** — из ответа API или минимальная дата в `GET /services` (`use_services_forecast: true` по умолчанию)
