# Mock (`mock`)

Тестовый плагин без запросов к внешнему API. Все значения задаются в `plugin_config`.

## Пример конфигурации

```yaml
- name: demo-service
  plugin: mock
  poll_interval_seconds: 300
  balance_threshold: 100.0
  subscription_warn_days: 7
  plugin_config:
    balance: 50.0
    currency: RUB
    subscription_end: "2026-06-01T00:00:00+00:00"
```

## Параметры `plugin_config`

| Поле | Обязательно | Описание |
|------|-------------|----------|
| `balance` | нет | Число для отображения и алертов |
| `currency` | нет | Валюта (по умолчанию `RUB`) |
| `subscription_end` | нет | ISO-дата окончания подписки |

## Откуда берутся данные

Все поля читаются напрямую из `plugin_config`, без HTTP-запросов.
