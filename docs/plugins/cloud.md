# Cloud.ru (`cloud`)

Мониторинг баланса и прогноза отключения по договору через [API Cloud.ru Evolution](https://cloud.ru/docs/billing/ug/topics/api-ref_start) (`organization.api.cloud.ru`).

Для каждого договора — отдельная запись в `services` со своим `agreement_id` и ключами доступа.

## Как получить доступ к API

### Персональный ключ (рекомендуется для личного аккаунта)

1. Войдите в [личный кабинет Cloud.ru](https://console.cloud.ru/).
2. Профиль → **Ключи доступа** (или раздел API в документации [Аутентификация](https://cloud.ru/docs/console_api/ug/topics/guides__auth_api.html)).
3. Создайте персональный ключ доступа, скопируйте **Key ID** и **Key Secret**.
4. Укажите в `plugin_config`: `key_id`, `key_secret` (режим `auth: key` по умолчанию).

### Сервисный аккаунт

1. Создайте сервисный аккаунт в разделе IAM.
2. Назначьте роли **Администратор организации** или **Администратор затрат** (для доступа к данным договора).
3. Создайте статический **API-ключ** для сервисного аккаунта.
4. В конфиге: `auth: api_key`, `api_token: "<ключ>"`.

### Готовый Bearer-токен

Если токен уже получен (например, через `curl` к `iam.api.cloud.ru`):

```yaml
auth: bearer
access_token: "..."
```

Токен не коммитьте в git; храните только в `config.yaml`.

## ID договора (`agreement_id`)

1. Личный кабинет → **Контроль затрат** → **Договор**.
2. На карточке договора скопируйте **ID договора** (см. [FAQ](https://cloud.ru/docs/billing/ug/topics/faq__api_id_agreement)).

Нужны права **Администратор организации** или **Администратор затрат**.

## Пример конфигурации

```yaml
- name: cloud-main
  plugin: cloud
  poll_interval_seconds: 3600
  balance_threshold: 500.0
  subscription_warn_days: 7
  plugin_config:
    key_id: "YOUR_KEY_ID"
    key_secret: "YOUR_KEY_SECRET"
    agreement_id: "00000000-0000-0000-0000-000000000000"
    customer_id: "00000000-0000-0000-0000-000000000001"  # опционально
    currency: RUB
    balance_field: balance   # balance | money | real | bonus | total
```

Несколько договоров — несколько сервисов с разными `agreement_id` и при необходимости разными ключами.

## Параметры `plugin_config`

| Поле | Обязательно | По умолчанию | Описание |
|------|-------------|--------------|----------|
| `agreement_id` | да | — | UUID договора из ЛК |
| `key_id` | да* | — | Key ID персонального ключа (`auth: key`) |
| `key_secret` | да* | — | Key Secret (`auth: key`) |
| `access_token` | да* | — | Bearer-токен (`auth: bearer`) |
| `api_token` | да* | — | API-ключ или Bearer (зависит от `auth`) |
| `auth` | нет | `key` | `key`, `bearer`, `api_key` |
| `customer_id` | нет | — | ID организации/клиента (опционально) |
| `base_url` | нет | `https://organization.api.cloud.ru` | Базовый URL billing API |
| `iam_url` | нет | `https://iam.api.cloud.ru/api/v1/auth/token` | URL обмена key → token |
| `balance_path` | нет | авто | Явный путь, например `v1/agreements/{agreement_id}/balance` |
| `currency` | нет | из ответа | Валюта для `/status` |
| `balance_field` | нет | `balance` | Какое поле считать балансом: `balance`, `money`, `real`, `bonus`, `total` |

\* Обязательность зависит от выбранного `auth`.

## Откуда берутся данные

| Поле бота | Источник API |
|-----------|--------------|
| `balance` | Ответ billing API по договору (поле из `balance_field`) |
| `subscription_end` | Дата из ответа API (`*_date`, `forecast`, …) или `now + days_left`, если API отдаёт только «дней хватит» |
| `currency` | из ответа или `plugin_config.currency` |

Авторизация: `Authorization: Bearer <token>` (после обмена key_id/key_secret) или `Authorization: Api-Key <ключ>`.

Плагин по умолчанию пробует несколько типовых путей (`/v1/agreements/{id}/balance`, `/v1/agreements/{id}`, …). Если у вас другой endpoint — укажите `balance_path`.

Публичная документация Cloud.ru подробно описывает API **потребления** (`/v1/consumption`, `/v2/consumption`); endpoint баланса в ЛК может отличаться по версии платформы — при ошибках уточните путь в поддержке Cloud.ru или через DevTools браузера в разделе «Контроль затрат».

## Частые проблемы

- **403 / нет доступа** — недостаточно ролей (нужен админ организации или админ затрат) или неверный договор.
- **Не удалось получить баланс** — задайте `balance_path` вручную по фактическому запросу из ЛК.
- **Нет `subscription_end`** — API не вернул дату/дни; проверьте виджет баланса в панели и поля ответа.
