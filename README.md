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

### Docker Compose (рекомендуется)

```bash
docker compose up -d --build
docker compose logs -f balance-bot
```

Остановка: `docker compose down`.

Конфиг и плагины монтируются с хоста:

- `./config.yaml` → `/config/config.yaml`
- `./plugins/` → `/plugins/`

После правок перезапустите: `docker compose restart balance-bot`.

### Локально (uv)

```bash
uv sync
uv run balance-bot -c config.yaml
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
- Документация по сервисам: [mock](plugins/mock.md), [vdsina](plugins/vdsina.md), [aeza](plugins/aeza.md)
- Шаблон для нового плагина: [PLUGIN_TEMPLATE.md](plugins/PLUGIN_TEMPLATE.md)
