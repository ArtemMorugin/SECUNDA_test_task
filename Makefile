SHELL := /bin/sh
.DEFAULT_GOAL := help
.NOTPARALLEL:

UV ?= uv
COMPOSE ?= docker compose
RUN := $(UV) run --frozen
TEST_COMPOSE := $(COMPOSE) -p secunda_checks -f docker-compose.test.yml
TEST_DATABASE_URL ?= postgresql+asyncpg://postgres:integration-test-password@localhost:15432/payments_test
TEST_RABBITMQ_URL ?= amqp://integration:integration-test-password@localhost:15673/
TEST_ENV = env TEST_DATABASE_URL='$(TEST_DATABASE_URL)' TEST_RABBITMQ_URL='$(TEST_RABBITMQ_URL)'
SERVICES ?= api consumer publisher

.PHONY: help env sync build up down restart ps logs infra \
	run-api run-consumer run-publisher migrate migrate-docker migration-check \
	lint format format-check typecheck check test test-unit test-infra \
	test-integration test-all smoke test-down

help:
	@printf '%s\n' \
	  'make up                 Build and start the complete Docker stack' \
	  'make down               Stop the stack; keep database volumes' \
	  'make ps                 Show service status' \
	  'make logs               Follow logs; SERVICES="api consumer publisher"' \
	  'make restart            Restart existing application containers' \
	  'make sync               Install locked Python dependencies' \
	  'make infra              Start PostgreSQL and RabbitMQ for local runs' \
	  'make migrate            Apply migrations locally' \
	  'make migrate-docker     Apply migrations in Docker' \
	  'make run-api            Run API locally on port 8000' \
	  'make run-consumer       Run RabbitMQ consumer locally' \
	  'make run-publisher      Run outbox and webhook workers locally' \
	  'make check              Lint, format check, types and unit tests' \
	  'make format             Fix lint and format Python source' \
	  'make test               Run unit tests' \
	  'make test-integration   Start isolated infrastructure; run integration' \
	  'make test-all           Check quality; run all tests with PostgreSQL/MQ' \
	  'make smoke              Start isolated stack; test API through webhook' \
	  'make test-down          Remove the isolated test stack'

env:
	@set -e; if [ ! -f .env ]; then \
	  umask 077; cp .env.example .env; \
	  printf '%s\n' 'Created .env; set API_KEY and passwords before deployment'; \
	fi

sync:
	$(UV) sync --frozen

build: env
	$(COMPOSE) build

up: env
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down --timeout 30

restart:
	$(COMPOSE) restart $(SERVICES)

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100 $(SERVICES)

infra: env
	$(COMPOSE) up -d --wait postgres rabbitmq

run-api:
	$(RUN) python -B -m src.presentation.cli.api

run-consumer:
	$(RUN) python -B -m src.presentation.cli.consumer

run-publisher:
	$(RUN) python -B -m src.presentation.cli.publisher

migrate:
	$(RUN) alembic upgrade head

migrate-docker: env
	$(COMPOSE) run --rm migrate

migration-check:
	$(RUN) alembic check

lint:
	$(RUN) ruff check src alembic tests

format:
	$(RUN) ruff check --fix src alembic tests
	$(RUN) ruff format src alembic tests

format-check:
	$(RUN) ruff format --check src alembic tests

typecheck:
	$(RUN) mypy src

check: lint format-check typecheck test-unit

test: test-unit

test-unit:
	$(RUN) python -B -m pytest tests/unit -q

test-infra: env
	$(TEST_COMPOSE) stop api consumer publisher
	$(TEST_COMPOSE) up -d --wait postgres rabbitmq

test-integration: test-infra
	$(TEST_ENV) $(RUN) python -B -m pytest tests/integration -q

test-all: lint format-check typecheck test-infra
	$(TEST_ENV) $(RUN) python -B -m pytest -q

smoke: env
	$(TEST_COMPOSE) up -d --build --wait
	$(TEST_ENV) $(RUN) python -B -m tests.smoke

test-down:
	$(TEST_COMPOSE) down --timeout 30
