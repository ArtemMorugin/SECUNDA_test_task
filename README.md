# Асинхронный процессинг платежей

FastAPI атомарно записывает Payment и outbox в PostgreSQL. Отдельный publisher доставляет событие в RabbitMQ. Consumer эмулирует обработку и одной транзакцией сохраняет итог и задание webhook. Webhook worker отправляет уведомления независимо от процессинга.

## Архитектура

Структура исходников следует именам слоёв из AGENTS.md:

```text
src/
  domain/
    entities/payment.py
    value_objects/payment.py
    exceptions.py
  application/
    use_cases/{payments,outbox,webhooks}.py
    dtos/payments.py
    interfaces/payments.py
  presentation/
    api/
      v1/
        routers/payments.py
        schemas/payment.py
      middleware.py
      security.py
      errors.py
      health.py
    cli/{api,consumer,publisher,healthcheck}.py
  infrastructures/
    db/
      models/{payment,outbox,webhook_delivery}.py
      repositories/{payment,outbox,webhook_delivery,transaction}.py
      base.py
    broker/
      publishers/outbox.py
      consumers/payments.py
      schemas.py
      topology.py
    webhooks/client.py
  config/
    ioc/{payments,broker,workers}.py
    settings.py
    database.py
    broker.py
    lifespan.py
    logging.py
    workers.py
  main.py
tests/{unit,integration}/
alembic/
Makefile
```

Domain содержит независимые сущности и значения. Application выполняет use cases, управляет транзакциями и обращается к портам из interfaces. Эти слои не импортируют FastAPI, Pydantic, SQLAlchemy, настройки или конкретные адаптеры. Presentation содержит HTTP-обработчики, валидацию API и команды запуска CLI; infrastructures реализует доступ к PostgreSQL, RabbitMQ и HTTP. Config собирает зависимости и управляет настройками/lifecycle. `src/main.py` собирает FastAPI-приложение.

Каждый независимый запрос/захват/сообщение получает собственную сессию. SQL-транзакции закрываются до sleep, broker publish и HTTP. DTO и domain entities отделены от ORM и Pydantic. Созданы только используемые пакеты; DI-фреймворк и generic repositories не требуются.

## Запуск через Makefile

Требуются Make, Docker Compose v2 и свободные порты 8000, 5432, 5672, 15672. Для локальной разработки и тестов нужны Python 3.14 и uv. Все команды запускаются из корня проекта; `make help` показывает доступные цели.

```bash
make env
# Укажите собственный API_KEY и пароли в .env.
make up
make ps
make logs
```

`make env` создаёт .env из .env.example, если файла ещё нет; существующий файл сохраняется. `make up` собирает образы, запускает postgres/rabbitmq/migrate/api/consumer/publisher и ожидает healthchecks. Publisher запускает также webhook worker. API ожидает PostgreSQL/миграции и может стартовать без RabbitMQ. Consumer ждёт RabbitMQ; publisher восстанавливает соединение самостоятельно.

| Команда | Действие |
|---|---|
| make up | Собрать и запустить стек, применить изменённые настройки контейнеров |
| make down | Остановить и удалить контейнеры, сохранив рабочие volumes |
| make restart | Перезапустить существующие приложения |
| make ps | Показать состояние сервисов |
| make logs | Смотреть логи API/consumer/publisher |
| make logs SERVICES=api | Смотреть только API |
| make migrate-docker | Применить миграции отдельным Docker-контейнером |
| make check | Ruff lint/format, mypy и unit-тесты |
| make test-all | Проверки качества и все тесты с изолированными PostgreSQL/RabbitMQ |
| make smoke | Полный изолированный стек и проверка API→webhook |
| make test-down | Удалить изолированный тестовый стек |

После изменения кода или `.env` выполните `make up`: он обновляет образ/окружение. `make restart` перезапускает уже созданные контейнеры с их прежним окружением.

Те же действия напрямую через Docker Compose, без Make. Перед первым запуском создайте `.env` из `.env.example`, если файла ещё нет, и укажите API_KEY и пароли.

```bash
# make up — сборка, миграции и запуск с ожиданием готовности
docker compose up -d --build --wait
# make ps — состояние сервисов
docker compose ps
# make logs — просмотр логов (Ctrl+C завершает просмотр)
docker compose logs -f --tail=100 api consumer publisher
```

| Make | Docker Compose |
|---|---|
| make down | `docker compose down --timeout 30` |
| make restart | `docker compose restart api consumer publisher` |
| make logs SERVICES=api | `docker compose logs -f --tail=100 api` |
| make migrate-docker | `docker compose run --rm migrate` |
| make infra | `docker compose up -d --wait postgres rabbitmq` |

API-документация:

- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc
- OpenAPI JSON: http://localhost:8000/api/openapi.json

В Swagger нажмите **Authorize**, вставьте значение `API_KEY` из `.env`, затем **Authorize → Close**. API key передаётся в `X-API-Key`; `Idempotency-Key` вводится отдельно в POST. RabbitMQ Management доступен на http://localhost:15672 с credentials из .env.

Локальный запуск (URL в .env указывают на localhost):

```bash
make sync
make infra
make migrate
make run-api
# В отдельных терминалах:
make run-consumer
make run-publisher
```

API запускается как `python -m src.presentation.cli.api`, consumer/publisher — соответствующие модули в `src.presentation.cli`. Локальный API и Docker API используют один порт 8000: при переходе к локальному запуску остановите Docker-приложения.

## Переменные окружения

| Переменная | Назначение / default |
|---|---|
| API_KEY | Обязательный непустой ключ HTTP API |
| DATABASE_URL | Обязательный async SQLAlchemy URL PostgreSQL+asyncpg |
| RABBITMQ_URL | Обязательный AMQP URL |
| POSTGRES_PASSWORD, RABBITMQ_USER, RABBITMQ_PASSWORD | Credentials Compose; пример содержит локальные демонстрационные значения |
| APP_NAME, APP_VERSION | Название и версия API |
| APP_DEBUG | false; общий DEBUG не используется, чтобы не конфликтовать с окружением |
| LOG_LEVEL | INFO; DEBUG / INFO / WARNING / ERROR / CRITICAL |
| PAYMENT_PROCESSING_MIN_DELAY / MAX_DELAY | 2.0 / 5.0 секунды, min <= max |
| WEBHOOK_TIMEOUT_SECONDS | 5.0 секунд |
| WEBHOOK_MAX_ATTEMPTS | 3, допустимо 1–3, включая первую попытку |
| BROKER_TIMEOUT_SECONDS | 5.0 секунд |
| DELIVERY_LEASE_SECONDS | 30.0; больше максимального I/O timeout более чем на 5 секунд |
| WORKER_POLL_SECONDS | 1.0 |
| RETRY_BASE_SECONDS | 1.0; задержки между попытками 1 и 2 секунды |
| RABBITMQ_PREFIX | payments, namespace exchange/очередей |
| CONSUME_LEGACY_QUEUE | true, перенос старых payments.process/payment.created в новую topology |

Compose переопределяет URL адресами `postgres`/`rabbitmq`; localhost-URL из .env нужны вне контейнера. Спецсимволы credentials в URL должны быть URL-encoded; для автоматически составляемых Compose URL используйте URL-safe пароли. Настройки не содержат встроенных credentials. Не коммитьте .env.

## Логирование

Стандартный Python `logging` настраивается централизованно в `src/config/logging.py`. Один консольный handler пишет в stdout; файловых handlers нет. Формат содержит время UTC с миллисекундами, уровень, роль сервиса, модуль, событие и доступные request_id/event_id/payment_id/delivery_id. Uvicorn и FastStream используют ту же конфигурацию. Для локального API используйте `python -m src.presentation.cli.api`, чтобы она применялась до запуска сервера.

`LOG_LEVEL=INFO` показывает запуск/остановку, завершённые операции и HTTP-запросы. DEBUG добавляет попытки доставки, пропущенные дубликаты и успешный health. WARNING отмечает HTTP 4xx и повторные попытки; ERROR — HTTP 5xx, исчерпанные попытки и сбои сервисов. Успех платежа/публикации/webhook логируется после фиксации состояния в БД; устаревший claim_token не создаёт ложный лог успеха.

API принимает UUID в `X-Request-ID`; при отсутствии или невалидном значении создаёт новый UUID и возвращает его в заголовке ответа. Контекст изолирован через ContextVar для параллельных запросов и обработчиков. HTTP-лог содержит метод, шаблон маршрута, статус и duration_ms. Тело, query string, произвольные пути, ключи, суммы, metadata и webhook URL не записываются. Стандартный access log Uvicorn отключён, чтобы не дублировать эти события и не выводить исходные URL.

Ошибки содержат класс исключения и кадры стека (файл, строка, функция), без текста исключения, исходного кода и локальных переменных. SQL-параметры не выводятся; настроенные секреты и URL маскируются, управляющие символы в сообщениях экранируются. В offline-режиме Alembic (`--sql`) лог пишет в stderr, сохраняя stdout для SQL.

```text
2026-10-15T12:00:00.123Z INFO     [api] src.presentation.api.middleware: http_request method=POST route=/api/v1/payments status=202 duration_ms=8.41 request_id=1c03aaf5-ae80-4c32-b6a3-c46404c09eae
```

## API

```bash
export API_KEY='ваш-ключ-из-.env'
curl -i -X POST http://localhost:8000/api/v1/payments \
  -H "X-API-Key: $API_KEY" \
  -H 'Idempotency-Key: order-123' \
  -H 'Content-Type: application/json' \
  -d '{"amount":"100.00","currency":"RUB","description":"Заказ 123","metadata":{"order_id":123},"webhook_url":"https://example.org/webhook"}'
```

Ответ 202: `{"payment_id":"UUID","status":"pending","created_at":"2026-10-15T00:00:00Z"}`.

```bash
curl -H "X-API-Key: $API_KEY" http://localhost:8000/api/v1/payments/UUID
curl -H "X-API-Key: $API_KEY" http://localhost:8000/health
```

GET содержит payment_id, amount, currency, description, metadata, status, idempotency_key, webhook_url, created_at, processed_at. Missing/invalid API key → 401; невалидные данные или отсутствующий Idempotency-Key → 422; неизвестный UUID → 404; ошибка SQLAlchemy → безопасный 503. Маршруты платежей и health требуют ключ; Swagger/OpenAPI остаются документацией.

Повтор с тем же ключом и параметрами возвращает тот же платёж (202 и актуальный status), включая суммы `100`/`100.00`. Другие параметры → 409. Конкурентное создание защищено PostgreSQL UNIQUE. Для старых платежей сравниваются сохранённые параметры, поэтому прежний request_hash не ломает replay.

## Outbox и процессинг

POST записывает pending Payment + OutboxEvent одной транзакцией без публикации. Событие версии 1 содержит event_id, event_type=payments.new, payment_id, amount, currency.

Publisher захватывает одну запись через FOR UPDATE SKIP LOCKED, устанавливает lease/token и увеличивает attempts до сетевого вызова. Публикует persistent/mandatory сообщение с message_id=event_id, ждёт confirm и обрабатывает basic.return как ошибку. До публикации объявляется вся topology; отсутствие запущенного consumer не теряет событие.

Отметки защищены claim_token. Падение после отправки до отметки published допускает повтор с тем же event_id; после истечения lease запись доступна снова. После трёх неудач/незавершённых попыток failed_at сохраняется, запись не удаляется; требуется явный повторный запуск. Ошибка подключения до захвата записи не расходует её попытки.

Consumer читает платёж, закрывает транзакцию, ждёт 2–5 секунд и выбирает succeeded (90%) либо failed (10%). Затем проверяет pending под FOR UPDATE и атомарно сохраняет статус, processed_at и WebhookDelivery. Завершённые платежи пропускаются. Конкурентные дубликаты могут выполнить эмуляцию, но фиксируется один итог и одно уведомление.

## Retry и DLQ

- payments.new — durable quorum; exchange payments — direct/durable.
- При технической ошибке: подтверждённая публикация в payments.retry.1 (TTL 1s), затем payments.retry.2 (TTL 2s). x-processing-attempt хранит номер, максимум 3.
- После TTL событие возвращается в payments.new. Retry-очереди используют at-least-once dead-lettering и reject-publish overflow.
- Невалидный JSON/контракт, неизвестный платёж и исчерпание → payments.dlq. Исходное сообщение ACK только после commit или подтверждённого handoff.
- При ошибке handoff — замедленный nack/requeue. Основная quorum queue имеет дополнительный delivery-limit=3 и безопасный DLX в DLQ для аварийных redelivery.
- Бизнес-результат failed не требует retry/DLQ.
- Старую classic payments.process не переобъявляем и не удаляем: сначала переносим её сообщения в новую очередь. После освобождения можно отключить CONSUME_LEGACY_QUEUE.

DLQ сохраняет исходное тело, идентификатор, номер попытки и тип ошибки. После устранения причины повторно публикуйте через RabbitMQ Management с тем же event_id/message_id. Удаляйте исходное сообщение только после подтверждения публикации. Завершённый платёж не создаст второй webhook.

## Webhook

HTTP выполняется после захвата постоянного задания, вне транзакции. Timeout и non-2xx считаются ошибкой. Worker сохраняет attempts, next_attempt_at, last_error (тип, без URL/credentials), delivered_at и status=pending/delivered/failed. После рестарта используются прежние запись и delivery_id. Ошибка доставки не меняет статус платежа.

```json
{"event":"payment.processed","delivery_id":"UUID","payment_id":"UUID","status":"succeeded","amount":"100.00","currency":"RUB","processed_at":"2026-10-15T00:00:00Z"}
```

HTTP Idempotency-Key равен delivery_id. Получателю нужна дедупликация: падение после HTTP 2xx до DB commit допускает повтор. После третьей попытки status=failed остаётся в БД. Падение во время последней попытки после lease expiry также приводит к failed, при этом результат внешнего HTTP может быть неизвестен.

## Повтор exhausted outbox/webhook

После восстановления зависимости сбросьте **конкретную** запись, сохранив ID:

```sql
UPDATE outbox
SET attempts = 0, failed_at = NULL, locked_until = NULL, claim_token = NULL,
    next_attempt_at = now(), last_error = NULL
WHERE id = 'EVENT_UUID' AND published_at IS NULL AND failed_at IS NOT NULL;

UPDATE webhook_deliveries
SET status = 'pending', attempts = 0, locked_until = NULL, claim_token = NULL,
    next_attempt_at = now(), last_error = NULL
WHERE id = 'DELIVERY_UUID' AND status = 'failed';
```

SQL запускается через `docker compose exec postgres psql -U postgres -d payments`. Не сбрасывайте активный lease и не меняйте payment.status ради webhook.

## Тесты и качество

Python-код ограничен 79 символами в строке, комментарии и docstrings — 72 по PEP 8. Ruff проверяет E501/W505 и корректное использование logging (G/LOG).

```bash
make sync
make check
# Только unit:
make test
# Только integration с изолированными PostgreSQL и RabbitMQ:
make test-integration
# Проверки качества и полный набор:
make test-all
# Автоисправления lint и форматирование:
make format
```

`make test-integration` и `make test-all` запускают отдельный Compose project secunda_checks на портах 15432/15673 с БД payments_test. Перед тестами останавливаются приложения только этого тестового проекта, чтобы workers не изменяли данные параллельно с pytest. Фикстура отказывается изменять БД без суффикса _test. Тесты очищают таблицы и выполняют downgrade/upgrade; не указывайте рабочую БД. Для переопределения тестовых подключений используются Make-переменные TEST_DATABASE_URL/TEST_RABBITMQ_URL.

Полный изолированный стек запускайте после pytest:

```bash
make smoke
# API/docs: http://localhost:18000/api/docs
# API key: integration-test-key; RabbitMQ Management: localhost:15683
make test-down
```

Smoke поднимает локальный HTTP receiver и использует host.docker.internal (Docker Desktop). На Linux добавьте publisher `extra_hosts: ["host.docker.internal:host-gateway"]` в тестовую конфигурацию. Проверяются новые docs URL, X-Request-ID, создание/чтение, replay/409 и доставка webhook.

Integration проверяет реальную конкуренцию, rollback payment/outbox и status/webhook, восстановление брокера, basic.return, повтор после падения publisher, consumer/retry/DLQ, HTTP 503/timeout/network errors, fencing старого worker и Alembic downgrade→upgrade→check.

## Ограничения

- Эмуляция без реального платёжного провайдера/списания.
- At-least-once допускает повторные сообщения и HTTP; exactly-once внешние эффекты не обещаются.
- После трёх попыток outbox/webhook нужен явный повтор; DLQ требует разбора причины.
- Старые завершённые платежи автоматически повторно не уведомляются: прежний факт доставки неизвестен.
- Одиночные PostgreSQL/RabbitMQ не защищают от потери диска; репликация, backup и мониторинг — эксплуатационные задачи.
- HttpUrl проверяет формат, но не ограничивает сеть. API рассчитано на доверенного владельца ключа; SSRF allowlist в ТЗ не предусмотрен.
