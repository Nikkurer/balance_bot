# Balance Bot

Telegram-бот для отслеживания баланса и срока подписки на разных сервисах с напоминаниями.

## Возможности

- Плагинная архитектура: каждый сервис — отдельный плагин
- Доступ только для указанных Telegram user ID
- Конфигурация в YAML-файле
- Текущее состояние сервисов в оперативной памяти; опционально — **история баланса** в SQLite (для графиков и анализа)
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

Конфиг и плагины в обоих случаях монтируются с хоста: `./config.yaml`, `./plugins/`. При включённой истории баланса — также `./data/` (SQLite).

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

### История баланса (SQLite)

По умолчанию бот хранит только **последний** снимок каждого сервиса (в RAM). Секция `history` включает запись успешных опросов в SQLite — для последующих графиков и экспорта.

```yaml
history:
  enabled: true
  path: data/balance_bot.db   # относительно каталога config.yaml
  retention_days: 365         # 0 — не чистить по времени
  max_size_mb: 32             # 0 — не чистить по размеру
  record_errors: false        # true — писать сбои опроса в таблицу poll_errors
  chart_points_per_day: 4     # 0 — все точки из БД; иначе усреднение при избытке за сутки
  chart_max_points: 10000     # 0 — без лимита; иначе последние N точек для /chart
  prune_interval_hours: 24    # 0 — без фонового prune
```

При `enabled: true` нужен **хотя бы один** из параметров очистки больше нуля (`retention_days` или `max_size_mb`). Значение `0` означает «не применять этот критерий».

Данные разделены на две таблицы:

- **`balance_history`** — успешные опросы (баланс, валюта, дата подписки, источник) — для графиков
- **`poll_errors`** — сбои опроса при `record_errors: true` (не попадают на график)

Очистка: самые старые записи удаляются **пачками по 1%** от числа строк; затем `incremental_vacuum`. Retention применяется к обеим таблицам. `prune` вызывается при открытии БД, после `/refresh` (`poll_all_now`) и по таймеру (`prune_interval_hours`, по умолчанию 24; `0` — без фонового prune).

**Docker:** каталог `./data` смонтирован в compose-файлах — файл БД переживает перезапуск контейнера. Каталог `data/` в git не коммитится.

**Локально:** создайте каталог рядом с конфигом (например `data/`) или укажите абсолютный `path`.

### График баланса (`/chart`)

Команда `/chart` строит PNG-график по данным из `balance_history`:

- `/chart` — выбор сервиса кнопками, затем периода (7 / 30 / 90 дней или всё)
- `/chart vdsina-ru` — сразу выбор периода
- `/chart vdsina-ru 30d` — график без кнопок

Имя сервиса (`services[].name`) — не длиннее **64 символов** (лимит текста inline-кнопки Telegram). В `callback_data` передаётся индекс, не имя.

На графике отображается число сбоев опроса за период (из `poll_errors`), если `record_errors: true`.

`chart_points_per_day` задаёт максимум точек на графике за календарные сутки (в timezone бота). Если за сутки в БД записей больше — баланс усредняется по интервалам. Если записей не больше лимита — на график попадают все точки из БД. Значение `0` отключает усреднение.

`chart_max_points` ограничивает число точек, загружаемых из БД для одного графика (по умолчанию 10 000; при превышении берутся последние по времени). Значение `0` снимает лимит.

## Плагины

Плагины лежат в каталоге `plugins/` (настраивается через `plugins_dir`). Добавление — копирование файла в эту папку и указание `plugin: <имя>` в конфиге. Код бота менять не нужно.

- Общее и контракт: [docs/plugins/README.md](docs/plugins/README.md)
- Документация по сервисам: [mock](docs/plugins/mock.md), [vdsina](docs/plugins/vdsina.md), [aeza](docs/plugins/aeza.md), [cloud](docs/plugins/cloud.md)
- Шаблон для нового плагина: [PLUGIN_TEMPLATE.md](docs/plugins/PLUGIN_TEMPLATE.md)

## CI/CD (GitHub)

При push и pull request в `main` запускается [GitHub Actions](.github/workflows/ci.yml): проверка Python, плагинов и сборка Docker-образа.

Подробно: развёртывание на сервер, секреты, опциональный autodeploy — [docs/ci-cd.md](docs/ci-cd.md).

При **публикации релиза** на GitHub образ автоматически пушится в GHCR — см. [docs/ci-cd.md](docs/ci-cd.md#5-автоматический-push-образа-при-релизе-ghcr).
