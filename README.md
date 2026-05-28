# Balance Bot

Telegram-бот для отслеживания баланса и срока подписки на разных сервисах с напоминаниями.

## Возможности

- Плагинная архитектура: каждый сервис — отдельный плагин
- Доступ только для указанных Telegram user ID
- Конфигурация в YAML-файле
- Состояние сервисов только в оперативной памяти (не пишется на диск)
- Индивидуальный интервал опроса для каждого сервиса
- Дата окончания подписки приходит от плагина/сервиса, бот её не вычисляет

## Быстрый старт

```bash
cp config.example.yaml config.yaml
# Отредактируйте config.yaml: токен бота, ваш user id, сервисы
```

### Docker Compose

Два файла:

| Файл | Назначение |
|------|------------|
| [`docker-compose.dev.yml`](docker-compose.dev.yml) | Сборка из исходников (`build: .`) — разработка и сервер с git-клоном |
| [`docker-compose.yml`](docker-compose.yml) | Готовый образ из [GHCR](docs/ci-cd.md#5-автоматический-push-образа-при-релизе-ghcr) — продакшен на сервере |

Конфиг и плагины в обоих случаях монтируются с хоста: `./config.yaml`, `./plugins/`.

**Разработка / сборка локально:**

```bash
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml logs -f balance-bot
```

**Продакшен (образ после релиза):**

```bash
cp .env.example .env
# В .env: BALANCE_BOT_IMAGE=ghcr.io/<owner>/<repo>:1.0.0
# Для диагностики можно включить debug-логи: BALANCE_BOT_DEBUG=1
docker compose pull
docker compose up -d
docker compose logs -f balance-bot
```

Остановка: `docker compose down` (для dev добавьте `-f docker-compose.dev.yml`).

После правок `config.yaml` или плагинов: `docker compose restart balance-bot` (или с `-f docker-compose.dev.yml`).

### Локально (uv)

```bash
uv sync
uv run balance-bot -c config.yaml
```

Debug-режим:

```bash
uv run balance-bot -c config.yaml --debug
# или
BALANCE_BOT_DEBUG=1 uv run balance-bot -c config.yaml
```

### Тесты

```bash
uv sync --group dev
uv run pytest
```

Узнать свой Telegram user id можно у [@userinfobot](https://t.me/userinfobot).

## Команды бота

| Команда   | Описание                          |
|-----------|-----------------------------------|
| `/start`  | Справка                            |
| `/status` | Текущее состояние всех сервисов   |
| `/refresh`| Принудительный опрос              |

## Конфигурация

```yaml
plugins_dir: plugins   # в Docker: /plugins
timezone: Europe/Moscow  # IANA timezone для времени в сообщениях и логах

telegram:
  bot_token: "..."
  allowed_user_ids: [123456789]

services:
  - name: "my-vps"
    plugin: mock              # имя плагина
    poll_interval_seconds: 600
    balance_threshold: 50.0   # опционально: алерт при балансе ниже
    subscription_warn_days: 7 # опционально: алерт за N дней до subscription_end
    plugin_config:            # параметры, специфичные для плагина
      ...
```

## Плагины

Плагины лежат в каталоге `plugins/` (настраивается через `plugins_dir`). Добавление — копирование файла в эту папку и указание `plugin: <имя>` в конфиге. Код бота менять не нужно.

- Общее и контракт: [plugins/README.md](plugins/README.md)
- Документация по сервисам: [mock](plugins/mock.md), [vdsina](plugins/vdsina.md), [aeza](plugins/aeza.md), [cloud](plugins/cloud.md)
- Шаблон для нового плагина: [PLUGIN_TEMPLATE.md](plugins/PLUGIN_TEMPLATE.md)

## CI/CD (GitHub)

При push и pull request в `main` запускается [GitHub Actions](.github/workflows/ci.yml): проверка Python, плагинов и сборка Docker-образа.

Подробно: развёртывание на сервер, секреты, опциональный autodeploy — [docs/ci-cd.md](docs/ci-cd.md).

При **публикации релиза** на GitHub образ автоматически пушится в GHCR — см. [docs/ci-cd.md](docs/ci-cd.md#5-автоматический-push-образа-при-релизе-ghcr).
