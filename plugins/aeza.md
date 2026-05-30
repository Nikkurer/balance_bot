# Aeza (`aeza`)

Мониторинг баланса и срока услуг для [aeza.ru](https://aeza.ru) / [aeza.net](https://aeza.net).

Аккаунты **.ru** и **.net** разные — нужны разные токены и параметр `site`.

## Как получить API-токен

### aeza.net

1. [my.aeza.net](https://my.aeza.net/) → **Настройки** → [API-ключи](https://my.aeza.net/settings/apikeys).
2. Создайте ключ (можно ограничить по IP).
3. Укажите в конфиге с `site: net`.

### aeza.ru

1. [my.aeza.ru](https://my.aeza.ru/) → настройки → API-ключи.
2. Токен в конфиге с `site: ru`.

Токены не взаимозаменяемы между `.ru` и `.net`. Не коммитьте в git.

## Выбор API (`site`)

| `site` | URL по умолчанию | Авторизация | Эндпоинт баланса |
|--------|------------------|-------------|-----------------|
| `net` (по умолчанию) | `https://core.aeza.net/api` | `Bearer` | `GET /desktop` |
| `ru` | `https://my.aeza.ru/api` | `X-API-Key` | `GET /desktop` |

Переопределение: `base_url` и/или `auth: bearer | api_key` в `plugin_config`.

## Пример конфигурации

```yaml
- name: aeza-net
  plugin: aeza
  poll_interval_seconds: 3600
  balance_threshold: 100.0
  subscription_warn_days: 7
  plugin_config:
    api_token: "..."
    site: net
    currency: RUB

- name: aeza-ru
  plugin: aeza
  poll_interval_seconds: 3600
  balance_threshold: 100.0
  subscription_warn_days: 7
  plugin_config:
    api_token: "..."
    site: ru
    currency: RUB
```

## Параметры `plugin_config`

| Поле | Обязательно | По умолчанию | Описание |
|------|-------------|--------------|----------|
| `api_token` | да | — | API-ключ или Bearer-токен |
| `site` | нет | `net` | `ru` или `net` |
| `base_url` | нет | из `site` | Явный базовый URL API |
| `auth` | нет | из URL | `bearer` или `api_key` |
| `currency` | нет | из API / конфига | Валюта для `/status` |
| `use_services_forecast` | нет | `true` | Искать дату в `GET /services`, если нет в desktop/accounts |

## Откуда берутся данные

| Поле бота | Источник |
|-----------|----------|
| `balance` | `GET /desktop`: поле ``balance.value`` в копейках/центах (÷100) |
| `subscription_end` | ответ `/desktop` или минимальная дата среди услуг в `/services` |
| `currency` | API или `plugin_config` |

## Частые проблемы

- **401 / ошибка авторизации** — для `site: net` нужен Bearer, для `ru` — API-ключ (`X-API-Key`); при необходимости задайте `auth` явно.
- **HTTP 500 на `/accounts`** — на `my.aeza.ru` эндпоинт может быть недоступен; плагин использует `/desktop` с тем же API-ключом.
- **Пустая дата подписки** — отключите `use_services_forecast: false` или проверьте, есть ли forecast в ответе API.
