# Cloud.ru (`cloud`)

Мониторинг баланса договора через BFF личного кабинета Cloud.ru (`console.cloud.ru/u-api/bff-console`).

Для каждого договора — отдельная запись в `services` со своим `agreement_id` и ключами доступа.

## Логика опроса

1. Запрашиваются **гранты** договора (`/v1/agreements/{id}/grants`) со статусами `READY` и `NOT_STARTED`.
2. Если есть **активный грант** (`BONUS_GRANT_STATUS_READY`):
   - **баланс** — сумма полей `current_amount` по всем READY-грантам;
   - **подписка до** — ближайший `expire_at` среди них.
3. Если активного гранта нет — запрашивается **баланс** (`/v2/agreements/{id}/balance`):
   - **баланс** — поле `balance` (рубли);
   - дата окончания неизвестна — в боте показывается **«--»**.

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
    currency: RUB
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
| `base_url` | нет | `https://console.cloud.ru/u-api/bff-console` | Базовый URL BFF console API |
| `iam_url` | нет | `https://iam.api.cloud.ru/api/v1/auth/token` | URL обмена key → token |
| `currency` | нет | `RUB` | Валюта для `/status` |

\* Обязательность зависит от выбранного `auth`.

## Откуда берутся данные

| Поле бота | Источник |
|-----------|----------|
| `balance` | Сумма `current_amount` активных грантов или поле `balance` из `/v2/.../balance` |
| `subscription_end` | `expire_at` активного гранта; при балансе без гранта — «--» |
| `currency` | `plugin_config.currency` (по умолчанию `RUB`) |

Авторизация: `Authorization: Bearer <token>` (после обмена key_id/key_secret) или `Authorization: Api-Key <ключ>`.

Фильтр статусов грантов передаётся повторяющимся query-параметром:
`?statuses=BONUS_GRANT_STATUS_READY&statuses=BONUS_GRANT_STATUS_NOT_STARTED`.

## Частые проблемы

- **403 / нет доступа** — недостаточно ролей (нужен админ организации или админ затрат) или неверный договор.
- **Ошибка grants/balance** — проверьте `agreement_id` и срок действия IAM-токена.
- **Подписка «--»** — нет активного гранта; отображается баланс договора, дату исчерпания средств API не отдаёт.
