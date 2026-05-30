# VDSina (`vdsina`)

Мониторинг баланса и прогноза отключения через [Public API VDSina](https://vdsina.ru/tech/api).

Аккаунты **vdsina.ru** и **vdsina.com** — разные: для каждого нужен свой токен и запись в `services`.

## Как получить API-токен

### vdsina.ru

1. Войдите в [панель управления](https://cp.vdsina.ru/) (или [my.vdsina.ru](https://my.vdsina.ru/)).
2. Откройте раздел аккаунта → **API**: [my.vdsina.ru/account/api](https://my.vdsina.ru/account/api).
3. Скопируйте постоянный токен API (или создайте/обновите, если панель предлагает).
4. Укажите в `config.yaml` для сервиса с `site: ru`.

### vdsina.com

1. Войдите в [панель vdsina.com](https://cp.vdsina.com/).
2. В настройках аккаунта найдите раздел **API** (аналогично `.ru`).
3. Скопируйте токен для этого аккаунта.
4. Укажите в конфиге для сервиса с `site: com`.

### Важно

- Токен привязан к пользователю панели: права API = права этого пользователя. Для ограниченного доступа создайте отдельного пользователя и возьмите токен от него.
- При смене пароля токен обычно сбрасывается — обновите `config.yaml`.
- Токен не коммитьте в git; храните только в `config.yaml`.

## Выбор API (`site`)

| `site` | API URL |
|--------|---------|
| `ru` (по умолчанию) | `https://userapi.vdsina.ru/v1` |
| `com`, `vdsina.com`, `.com` | `https://userapi.vdsina.com/v1` |

Явный URL: `base_url` в `plugin_config` (переопределяет `site`).

## Пример конфигурации

```yaml
- name: vdsina-ru
  plugin: vdsina
  poll_interval_seconds: 3600
  balance_threshold: 500.0
  subscription_warn_days: 7
  plugin_config:
    api_token: "..."
    site: ru
    currency: RUB
    balance_field: real

- name: vdsina-com
  plugin: vdsina
  poll_interval_seconds: 3600
  balance_threshold: 10.0
  subscription_warn_days: 7
  plugin_config:
    api_token: "..."
    site: com
    currency: USD
    balance_field: real
```

## Параметры `plugin_config`

| Поле | Обязательно | По умолчанию | Описание |
|------|-------------|--------------|----------|
| `api_token` | да | — | Токен Public API |
| `site` | нет | `ru` | Регион: `ru` или `com` |
| `base_url` | нет | из `site` | Явный базовый URL API |
| `currency` | нет | — | Валюта для отображения в `/status` |
| `balance_field` | нет | `real` | Какой баланс мониторить: `real`, `bonus`, `partner`, `total` |

## Откуда берутся данные

| Поле бота | Источник API |
|-----------|--------------|
| `balance` | `GET /account.balance` → поле из `balance_field` |
| `subscription_end` | `GET /account` → `forecast` (дата отключения от API) |
| `currency` | из `plugin_config` (API не отдаёт единообразно) |

Авторизация: `Authorization: Bearer <api_token>`.

## Частые проблемы

- **Unknown plugin / ошибка API** — неверный токен или токен от другого сайта (`.ru` vs `.com`).
- **Низкий баланс в алерте, а в панели иначе** — проверьте `balance_field` (`real` vs `total` с бонусами).
