COMPOSE ?= docker compose

.PHONY: up down logs ps migrate test lint format shell api worker executor

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) run --rm api python -m alembic upgrade head

test:
	$(COMPOSE) run --rm api pytest

lint:
	$(COMPOSE) run --rm api ruff check src tests

format:
	$(COMPOSE) run --rm api ruff format src tests

shell:
	$(COMPOSE) run --rm api bash

api:
	$(COMPOSE) up --build api

worker:
	$(COMPOSE) up --build worker

executor:
	$(COMPOSE) up --build executor
