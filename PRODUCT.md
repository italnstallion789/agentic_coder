# Agentic Coder — Product Reference

## Overview

Agentic Coder is a local-first, GitHub-native autonomous AI development platform. It runs as a self-hosted service that monitors a dedicated **control repository** on GitHub, picks up natural-language engineering tasks posted as issue comments, and autonomously plans, codes, reviews, and opens pull requests in one or more **target repositories** — all without requiring a public webhook endpoint.

The platform is designed around a human-in-the-loop approval model: in `gated` mode (default), every proposed change waits for explicit sign-off via a GitHub comment before a PR is opened. In `autonomous` mode, the agent acts end-to-end without pausing.

---

## Architecture

### Services (Docker Compose)

| Service | Role |
|---|---|
| `api` | FastAPI control plane — webhooks, task management, dashboard |
| `worker` | Polling loop and task pipeline executor |
| `executor` | Sandbox execution for running tests and shell commands |
| `postgres` | Durable state store (tasks, runs, events, transitions, poll cursors) |
| `redis` | Task queue between API/webhook receiver and worker |
| `ollama` *(optional)* | Local LLM fallback (disabled by default when GitHub Models key is set) |

### High-Level Flow

```
GitHub Issue Comment
      │
      ▼
Worker polling loop (every N seconds)
      │  detects command syntax (@agent, repo=, /approve, /reject)
      ▼
Task normalisation → PostgreSQL + Redis queue
      │
      ▼
Worker dequeues task
      │
      ▼
TaskPipeline
  ├── PlannerAgent        (LLM: plan objective + steps)
  ├── ContextRetrievalAgent (index workspace .py files, keyword retrieval)
  ├── KnowledgeGraphBuilder (file/symbol graph)
  ├── CodingAgent         (LLM: propose files and summary)
  ├── ReviewerAgent       (LLM: approve/reject proposal)
  ├── SecurityAgent       (LLM + static: scan for risks)
  ├── TestAgent           (LLM: generate validation commands)
  └── PullRequestGenerator (LLM: write PR title + body)
      │
      ▼
[gated mode] Post approval-request comment → wait for /approve
[autonomous mode] Proceed immediately
      │
      ▼
Create branch → commit artifact → open draft PR in target repo
      │
      ▼
Post status update comment on source issue
```

---

## Data Model

### Task States

```
RECEIVED → NORMALIZED → INDEXED → PLANNED → AWAITING_APPROVAL → READY → RUNNING → SUCCEEDED
                                          ↘ (autonomous mode) ↗               ↘ FAILED
                                                                                ↘ CANCELLED
```

| State | Meaning |
|---|---|
| `received` | Task created from webhook or poll |
| `normalized` | Payload extracted and validated |
| `indexed` | Workspace and context indexed |
| `planned` | Pipeline has produced a plan |
| `awaiting_approval` | Gated mode: waiting for human `/approve` |
| `ready` | Approved or autonomous — ready to execute |
| `running` | Pipeline actively running |
| `succeeded` | PR created successfully |
| `failed` | Pipeline or PR creation error |
| `cancelled` | Rejected or manually cancelled |

### Database Tables

| Table | Purpose |
|---|---|
| `tasks` | One row per task; holds payload JSONB and current state |
| `runs` | One run per task execution; holds metadata and status |
| `task_transitions` | Append-only log of every state change with reason and details |
| `run_events` | Granular event timeline per run (plan_created, proposal_generated, pr_draft, etc.) |
| `poll_cursors` | Durable per-repo polling position (last `updated_at` seen) |

---

## Agent Pipeline

All agents receive the same `ModelProvider` instance resolved at pipeline init. Each agent has a model-backed implementation and a stub fallback used when the model call fails or no provider is configured.

### PlannerAgent
- **Input:** issue title + body
- **Output:** `PlanResult` — objective string + ordered list of implementation steps
- **Model prompt:** produces JSON `{objective, steps}`

### ContextRetrievalAgent
- **Input:** workspace root path
- **Output:** list of `RetrievalDocument` (ranked by keyword overlap with query)
- **No LLM** — indexes all `.py` files in workspace, keyword-scores against query terms

### KnowledgeGraphBuilder
- **Input:** workspace root
- **Output:** `{nodes, edges}` summary counts
- **No LLM** — walks directory tree, builds file/symbol graph nodes

### CodingAgent
- **Input:** `PlanResult` + context documents + repository name
- **Output:** `PatchProposal` — summary of changes + list of target file paths
- **Model prompt:** produces JSON `{summary, target_files}` constrained to files present in context

### ReviewerAgent
- **Input:** `PatchProposal`
- **Output:** `ReviewResult` — approved bool + feedback string
- **Model prompt:** produces JSON `{approved, feedback}`

### SecurityAgent
- **Input:** issue/comment body text
- **Output:** `SecurityResult` — passed bool + findings list
- **Dual check:** static marker scan (rm -rf, drop table, disable auth) AND model scan; fails if either fails; findings are merged

### TestAgent
- **Input:** `PlanResult` + `PatchProposal`
- **Output:** `ExecutionTestPlan` — ordered list of shell commands
- **Model prompt:** produces JSON `{commands}` targeted to affected files

### PullRequestGenerator
- **Input:** repo name + `PlanResult` + `PatchProposal`
- **Output:** `PullRequestDraft` — title + markdown body
- **Model prompt:** produces JSON `{title, body}` with sections: Summary, Changes, Testing

---

## Model Configuration

### Provider Abstraction

All LLM calls go through `ModelProvider` → `ModelRouter`. Two providers are implemented:

| Provider | Class | Endpoint |
|---|---|---|
| GitHub Models (Copilot) | `GitHubHostedProvider` | `https://models.inference.ai.azure.com` |
| Ollama (local) | `OllamaProvider` | `http://ollama:11434` |

Provider selection is controlled by `agentic.yaml`:

```yaml
models:
  primary_provider: github   # github | ollama | auto
  fallback_provider: null    # null to disable fallback
  embedding_provider: github
```

`auto` prefers GitHub if `GITHUB_MODELS_API_KEY` is set, otherwise falls back to Ollama.

### Current Model

| Setting | Value |
|---|---|
| Provider | GitHub Models (Copilot) |
| Model | `Phi-4` |
| Reason | Best free-tier model for software development tasks — optimised for code reasoning |
| Auth | Classic PAT (`ghp_...`) with active Copilot subscription |

### Changing the Model

Set `GITHUB_MODELS_CHAT_MODEL` in `.env`:

```
GITHUB_MODELS_CHAT_MODEL=Phi-4
```

Free-tier (0x) models available:

| Model | Name |
|---|---|
| Phi-4 *(current — recommended for code)* | `Phi-4` |
| Phi-4 mini | `Phi-4-mini` |
| GPT-4.1 mini | `gpt-4.1-mini` |
| GPT-4o mini | `gpt-4o-mini` |
| Meta Llama 3.3 70B | `Meta-Llama-3.3-70B-Instruct` |

Premium models (consume Copilot quota):

| Model | Name |
|---|---|
| GPT-4.1 | `gpt-4.1` |
| Claude 3.7 Sonnet | `claude-3-7-sonnet` |

---

## Policy Configuration (`agentic.yaml`)

```yaml
version: 1

system:
  name: agentic-coder
  control_repository: owner/agentic_coder      # repo the agent polls for commands
  allow_any_target_repository: false           # must be false in production
  allowed_target_repositories:
    - myorg/myrepo                             # explicitly allowed targets
  local_repository_paths:
    myrepo: /repos/myrepo                      # workspace path inside container
  default_target_base_branch: develop
  target_base_branches:
    myorg/myrepo: develop

autonomy:
  mode: gated                                  # gated (requires /approve) | autonomous
  allowed_actions:
    - plan
    - retrieve_context
    - build_knowledge_graph
    - write_patch
    - run_tests
    - create_pr
  approval_required_actions:
    - execute_shell
    - merge_pr
    - enable_network

sandbox:
  profile: compose
  network_enabled: false
  max_runtime_seconds: 900
  max_cpu_cores: 2
  max_memory_mb: 2048

models:
  primary_provider: github
  fallback_provider: null
  embedding_provider: github

trigger:
  mode: polling                                # polling | webhook | hybrid
  poll_interval_seconds: 60
  max_items_per_poll: 50

knowledge_graph:
  enabled: true
  storage: postgres
  node_types: [repo, commit, file, symbol, test, issue, pr, task, artifact, decision]
  edge_types: [contains, imports, depends_on, tests, resolves, generates]
```

---

## Trigger Modes

### Polling (default)

- Worker polls `system.control_repository` issue comments on a timer
- No public webhook endpoint required — works behind NAT/firewall
- Durable cursor stored in `poll_cursors` table — survives restarts
- Bot comments automatically filtered out to prevent feedback loops

### Webhook

- API receives `POST /webhook` with GitHub `X-Hub-Signature-256` verification
- HMAC-SHA256 validation using `GITHUB_WEBHOOK_SECRET`
- Immediately enqueues task to Redis

### Hybrid

- Both polling and webhook active simultaneously

---

## Command Syntax (GitHub Comments)

### Triggering a Task

```
@agent repo=owner/reponame
Description of the change to make
```

```
/repo owner/reponame
Description of the change to make
```

```
@agent repo=reponame add a health check endpoint
```

### Approving a Task (gated mode)

```
/approve
```
Approves the latest `awaiting_approval` task on this issue.

```
/approve <task_id>
/approve task=<task_id>
```
Approves a specific task by ID.

### Rejecting a Task

```
/reject reason goes here
/reject <task_id> reason goes here
```

---

## GitHub App Requirements

### Required Permissions

| Permission | Level |
|---|---|
| Metadata | Read |
| Contents | Read & Write |
| Pull requests | Read & Write |
| Issues | Read & Write |

### Required Events (webhook mode)

- `issue_comment`

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/readyz` | Readiness probe (DB + Redis) |
| `GET` | `/self-check` | Full startup diagnostics |
| `POST` | `/webhook` | GitHub App webhook receiver |
| `POST` | `/tasks` | Create task manually |
| `GET` | `/tasks` | List recent tasks |
| `GET` | `/tasks/{task_id}` | Task detail |
| `POST` | `/tasks/{task_id}/approve` | Approve a gated task |
| `POST` | `/tasks/{task_id}/reject` | Reject a task |
| `GET` | `/runs/{run_id}` | Run detail |
| `GET` | `/runs/{run_id}/events` | Run event timeline |
| `POST` | `/runs/{run_id}/pull-request` | Manually trigger PR creation for a run |
| `GET` | `/dashboard` | HTML dashboard UI |
| `GET` | `/dashboard/data` | Dashboard JSON data |

---

## Operations Dashboard

Available at `http://localhost:8080/dashboard`.

Displays per-task:
- Request title, body, and sender
- Source (control) and target repository
- Current task state and run status
- PR draft title and opened PR link (once created)
- Model used for the run
- Polling mode and last poll timestamp

---

## Security Controls

### Cross-Repo Path Leak Detection

If the agent proposes edits to files belonging to the control/source repository instead of the target repository, the task is immediately failed with event `proposal_target_mismatch`. This prevents the agent from accidentally modifying its own codebase.

### Bot Comment Filtering

All comments from GitHub App bots (`[bot]` suffix or `type: Bot`) are skipped during polling to prevent infinite approval loops.

### Policy Enforcement

- `allow_any_target_repository: false` — explicit allowlist required
- `approval_required_actions` — shell execution and PR merging always require human approval regardless of autonomy mode
- `network_enabled: false` — sandbox network disabled by default

### Webhook Security

All incoming webhooks are validated with HMAC-SHA256 using `GITHUB_WEBHOOK_SECRET` before any processing.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `development` | Environment name |
| `API_ADMIN_TOKEN` | — | Optional admin token for privileged API routes |
| `GITHUB_APP_ID` | — | GitHub App numeric ID |
| `GITHUB_WEBHOOK_SECRET` | — | HMAC secret for webhook verification |
| `GITHUB_PRIVATE_KEY` | — | PEM private key (inline) |
| `GITHUB_PRIVATE_KEY_PATH` | — | Path to PEM file (alternative to inline) |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | GitHub API base |
| `GITHUB_MODELS_API_KEY` | — | Classic PAT with Copilot subscription |
| `GITHUB_MODELS_BASE_URL` | `https://models.inference.ai.azure.com` | GitHub Models endpoint |
| `GITHUB_MODELS_CHAT_MODEL` | `gpt-4.1-mini` | Model name (override in .env) |
| `DATABASE_URL` | `postgresql+psycopg://...` | PostgreSQL connection string |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string |
| `QUEUE_NAME` | `agentic:tasks` | Redis list key for task queue |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama endpoint (if enabled) |
| `OLLAMA_CHAT_MODEL` | `llama3.1:8b` | Ollama model (if enabled) |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | `40.0` | Per-request LLM timeout |

---

## Development

### Quick Start

```bash
make bootstrap   # start stack + run migrations + self-check
make test        # run pytest in container
make logs        # stream all service logs
make lint        # ruff linting
```

### Running Tests Locally

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

### Migrations

```bash
make migrate                                      # apply pending migrations
alembic revision --autogenerate -m "description" # generate new migration
```

### Adding a New Agent

1. Create `src/agentic_coder/agents/my_agent.py` with a result dataclass and agent class
2. Accept `model: ModelProvider | None = None` in `__init__`
3. Implement `_*_with_model()` and `_*_stub()` methods
4. Add result field to `PipelineResult` dataclass in `orchestration/pipeline.py`
5. Instantiate the agent in `TaskPipeline.__init__` passing `_model`
6. Call it in `TaskPipeline.run()`
