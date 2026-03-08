# Agentic Coder

A local-first autonomous AI development platform scaffold.

## Current status

This repository contains the initial implementation foundation for:

- FastAPI control plane
- GitHub App webhook verification scaffolding
- YAML-based autonomy policy
- durable task state machine primitives
- model provider abstraction
- in-memory knowledge graph and retrieval prototypes
- sandbox execution policy scaffolding
- audit log primitives

## Development workflow

This project can be used without a local Python environment.

### Container-first development

Use Docker Compose as the primary development environment:

- `api`
- `worker`
- `executor`
- `postgres`
- `redis`
- optional `ollama`

Recommended workflow:

1. Open the repository in the provided dev container.
2. Run services with Docker Compose.
3. Execute tests and linting inside the container.

Quick commands (default path):

- `make up` start local stack
- `make down` stop local stack
- `make logs` stream service logs
- `make migrate` run alembic migrations
- `make bootstrap` start stack + migrate + run startup self-check
- `make test` run pytest in container
- `make lint` run ruff in container
- `make shell` open shell in api container

Notes:

- API is exposed on host port `8080`.
- Postgres and Redis are internal to the Compose network by default (prevents host port conflicts).

### Initial bootstrap

1. `make bootstrap`
2. `make test`

Webhook task ingestion now persists tasks in PostgreSQL and enqueues task IDs in Redis for worker processing.

### GitHub PR workflow

The API supports creating a draft PR from an existing run timeline:

- `POST /runs/{run_id}/pull-request`
- body: `{"installation_id": <int>, "branch_name": "agentic/<run>", "draft": true}`

This flow uses GitHub App installation token exchange, resolves the repository default branch, creates the head branch, and opens a draft PR using run-generated PR draft metadata.

### Control repo vs target repos

This agent can run from a dedicated control repository and execute development work in other repositories.

- Default target repo: webhook source repo (`payload.repository.full_name`)
- Explicit target override in issue/PR comment body:
	- `repo=owner/name`
	- `/repo owner/name`
- Policy gate in [agentic.yaml](agentic.yaml):
	- `system.allow_any_target_repository`
	- `system.allowed_target_repositories`

For production, keep `allow_any_target_repository: false` and explicitly list allowed targets.

### Where to configure model + repo targeting

Primary configuration is in [agentic.yaml](agentic.yaml):

- `models.primary_provider`: set to `auto` (current default)
- `models.fallback_provider`: fallback model path
- `system.allow_any_target_repository`: broad target access toggle
- `system.allowed_target_repositories`: explicit allowlist (supports `owner/repo` or bare repo name)

Current defaults are configured to allow only `predictiv` as a target repository.

### Startup self-check

Startup GitHub integration validation is enabled by default.

- `GET /startup/self-check` returns the last startup check result
- `POST /startup/self-check/run` reruns checks on demand

Checks include:

- GitHub credential presence (`GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`, webhook secret)
- GitHub App API access (`GET /app`)
- Per-repository installation + default branch checks for configured control/target repos

Environment flags:

- `GITHUB_STARTUP_SELF_CHECK=true`
- `GITHUB_STARTUP_SELF_CHECK_FAIL_FAST=false`

Set fail-fast to `true` when deploying production workers that should not run with broken GitHub integration.

### GitHub integration setup checklist

1. Create a GitHub App in your org/user settings.
2. Set permissions:
	- Metadata: Read
	- Contents: Read & Write
	- Pull requests: Read & Write
	- Issues: Read
3. Enable webhook and set webhook URL to your API `/github/webhook` endpoint.
4. Copy values into runtime env:
	- `GITHUB_APP_ID`
	- `GITHUB_PRIVATE_KEY`
	- `GITHUB_WEBHOOK_SECRET`
5. Install the app on:
	- your control repo
	- `predictiv`
6. Verify with:
	- `GET /startup/self-check`

If `make` is unavailable, use direct Compose commands:

- `docker compose up --build -d`
- `docker compose run --rm api pytest`
- `docker compose run --rm api ruff check src tests`

The repository includes a `.devcontainer` configuration so VS Code can attach directly to the `api` service and use the container interpreter for analysis and testing.

### Local Python environment

A host Python environment is optional and only needed if container-based development is not desired.

## Immediate next steps

- add PostgreSQL-backed persistence
- wire Redis queue and worker loop
- implement GitHub App installation token flow
- persist audit events and graph state
- replace prototype retrieval with pgvector-backed hybrid search
