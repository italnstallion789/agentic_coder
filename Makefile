COMPOSE ?= docker compose

.PHONY: up down logs ps migrate bootstrap test lint format shell api worker executor test-regression smoke qa

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

bootstrap:
	$(COMPOSE) up --build -d
	$(COMPOSE) run --rm api python -m alembic upgrade head
	$(COMPOSE) run --rm api python -c "import asyncio, json; from agentic_coder.api.main import run_github_self_check; print(json.dumps(asyncio.run(run_github_self_check()).model_dump(), indent=2))"

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

test-regression:
	$(COMPOSE) run --rm api ruff check src tests scripts
	$(COMPOSE) run --rm api python -m pytest tests -q

smoke:
	$(COMPOSE) up --build -d postgres redis api
	$(COMPOSE) run --rm api python -m alembic upgrade head
	$(COMPOSE) run --rm -e BASE_URL=http://api:8080 api python scripts/smoke_test.py --base-url http://api:8080 --target-repository predictiv --wait-seconds 180

qa: test-regression smoke
