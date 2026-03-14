# Agentic Coder

Agentic Coder is a local-first, GitHub-native agent orchestration service for turning remote requests into tracked engineering runs.

## Current status

The current implementation includes:

- FastAPI control plane with health, readiness, dashboard, timeline, and self-check endpoints
- PostgreSQL-backed tasks, runs, run events, transitions, polling cursors, chat sessions, and chat messages
- Worker polling against a dedicated control repository with remote `/approve` and `/reject` handling
- GitHub App integration for status comments, approval comments, branch creation, and draft PR opening
- Chat control plane at `/chat` for remote operator-driven task intake
- Automated regression, smoke, and CI coverage

## Current execution scope

The pipeline currently plans, retrieves context, reviews, generates validation commands, and drafts PR metadata.

- The current PR flow always creates a target-repo branch, commits `.agentic/runs/<run_id>.md`, and opens a draft PR
- When the coding proposal includes concrete file contents, those files are written into the PR branch before the PR opens
- If the proposal does not include concrete file contents, the PR remains artifact-only
- Patch application is therefore present, but it is still limited by proposal quality and the current context window

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
- `make test-regression` run lint + full pytest suite
- `make smoke` run runtime smoke tests against live API container
- `make qa` run regression + smoke end-to-end
- `make shell` open shell in api container

Notes:

- API is exposed on host port `8080`.
- Postgres and Redis are internal to the Compose network by default (prevents host port conflicts).

### Initial bootstrap

1. `make bootstrap`
2. `make qa`

`make bootstrap` starts the stack, applies migrations, and runs the GitHub startup self-check. `make qa` then runs lint, tests, and live smoke validation.

### Operating the system

There are two supported operator paths:

- GitHub comment workflow: comment on the control repository with `@agent repo=...` or `/repo ...`
- Chat workflow: open `http://localhost:8080/chat` and create a session for an allowed target repository

Remote approvals remain GitHub-native in `gated` mode:

- Comment `/approve` or `/approve <task_id>` on the control-repo issue thread
- Comment `/reject <task_id> reason` or `/reject reason`
- Chat executions in `gated` mode require an approval issue number so the worker knows where to post approval and status comments
- The chat UI exposes a model dropdown sourced from the backend runtime, with cost labels:
  - `0x`: no premium Copilot token charge
  - `1x`: premium Copilot token charge
  - `local`: local Ollama runtime

### GitHub PR workflow

The API supports creating a draft PR from an existing run timeline:

- `POST /runs/{run_id}/pull-request`
- body: `{"installation_id": <int>, "branch_name": "agentic/<run>", "draft": true}`

This flow uses GitHub App installation token exchange, resolves the repository default branch, creates the head branch, commits the run artifact, and opens a draft PR using run-generated PR draft metadata.
When the run includes concrete `file_changes`, those files are committed to the branch before the run artifact is written.

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

### Polling mode (private-by-default)

The default trigger mode is polling, which means no public webhook endpoint is required.

Design summary:

- worker polls GitHub issue comments from `system.control_repository`
- comments are converted into normalized tasks only when command syntax is present (`@agent`, `repo=...`, `/repo ...`)
- target repos are constrained by `system.allowed_target_repositories`
- polling cursor and poll-cycle metrics are durably stored in PostgreSQL (`poll_cursors`)

- Trigger config in [agentic.yaml](agentic.yaml):
	- `trigger.mode: polling|webhook|hybrid`
	- `trigger.poll_interval_seconds`
	- `trigger.max_items_per_poll`
- Poll source repo:
	- `system.control_repository`

Polling currently ingests issue comments from the control repository and creates tasks for comments that include agent commands (`@agent`, `repo=...`, or `/repo ...`).

Polling observability endpoint:

- `GET /polling/status`
	- returns configured trigger mode/interval/max-items
	- returns current cursor key and last poll metadata (`last_polled_at`, `last_seen_count`, `last_enqueued_count`, `since`, `last_comment_id`)

Example:

- `curl http://localhost:8080/polling/status`

### Where to configure model + repo targeting

Primary configuration is in [agentic.yaml](agentic.yaml):

- `models.primary_provider`: current default is `github`
- `models.fallback_provider`: fallback model path
- `system.allow_any_target_repository`: broad target access toggle
- `system.allowed_target_repositories`: explicit allowlist (supports `owner/repo` or bare repo name)

Current defaults are configured to allow only `predictiv` as a target repository.

The current local environment is configured to use `Phi-4` via `GITHUB_MODELS_CHAT_MODEL`.

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
2. Fill the required URL fields in GitHub App settings:
	- Homepage URL: any valid URL you control (for local dev you can use your GitHub profile/repo URL)
	- Webhook URL: public URL to this service endpoint `/github/webhook`
	  - local dev requires a tunnel (for example `https://<subdomain>.ngrok.app/github/webhook`)
	- Callback URL: only needed if you enable user-to-server OAuth flow; otherwise leave unset
3. Set permissions:
	- Metadata: Read
	- Contents: Read & Write
	- Pull requests: Read & Write
	- Issues: Read & Write
4. Enable webhook and set webhook secret.
5. Copy values into runtime env:
	- `GITHUB_APP_ID`
	- `GITHUB_WEBHOOK_SECRET`
	- choose one private key option:
	  - `GITHUB_PRIVATE_KEY` (inline PEM value)
	  - `GITHUB_PRIVATE_KEY_PATH` (path to PEM file mounted in container)
6. Install the app on:
	- your control repo
	- `predictiv`
7. Verify with:
	- `GET /startup/self-check`
	- `GET /polling/status`

If you run polling mode only, webhook delivery is optional. You can still set `GITHUB_WEBHOOK_SECRET` now and switch to `trigger.mode: webhook` or `hybrid` later.

If `make` is unavailable, use direct Compose commands:

- `docker compose up --build -d`
- `docker compose run --rm api pytest`
- `docker compose run --rm api ruff check src tests scripts`
- `docker compose run --rm -e BASE_URL=http://api:8080 api python scripts/smoke_test.py --base-url http://api:8080 --target-repository predictiv`

The repository includes a `.devcontainer` configuration so VS Code can attach directly to the `api` service and use the container interpreter for analysis and testing.

### Local Python environment

A host Python environment is optional and only needed if container-based development is not desired.

## Recommended next investments

- Improve patch fidelity so the coding agent can reliably produce complete file updates for more than the current narrow context window
- Execute generated validation commands in the sandbox executor before PR open
- Reduce worker idle-log noise or make polling log verbosity configurable
- Strengthen retrieval and knowledge-graph quality beyond the current `.py`-centric heuristics
