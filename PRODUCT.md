# Agentic Coder — Product Reference

## Overview

Agentic Coder is a self-hosted AI engineering control plane.

Its primary job is no longer to act as a homegrown replacement for GitHub's coding worker. The stronger product shape is:

- `Agentic Coder` handles intake, clarification, policy, audit trail, model routing, and operator UX
- `GitHub Copilot coding agent` handles most implementation work
- the local worker pipeline remains available as a fallback backend

This lets you run a private remote endpoint over your own network or Tailscale while still using GitHub's native issue, branch, PR, and review flows for the actual coding work.

---

## Product Positioning

### What the product is

- private AI engineering manager
- remote chat intake surface
- policy and governance layer
- run/event/task audit system
- dispatch layer into GitHub coding agent
- optional local execution fallback

### What the product is not

- a full replacement for GitHub's coding agent
- a local clone of GitHub's implementation engine
- a substitute for GitHub's PR review and merge UX

---

## Architecture

### Services (Docker Compose)

| Service | Role |
|---|---|
| `api` | FastAPI control plane — chat, dashboard, health, self-check, task APIs |
| `worker` | Polling loop and local-pipeline executor |
| `executor` | Sandbox execution for local shell/test operations |
| `postgres` | Durable state store for tasks, runs, events, cursors, sessions, and messages |
| `redis` | Queue for local worker execution |
| `ollama` *(optional)* | Local LLM runtime for manager/planning or local execution fallback |

### Primary Operating Modes

#### 1. Chat -> GitHub coding agent (default)

```text
Remote Operator
      │
      ▼
/chat session
      │  create session + add messages
      ▼
ChatManagerAgent
  ├── clarifies request
  ├── decides ready vs needs-more-info
  └── prepares GitHub issue payload
      │
      ▼
GitHub issue creation + coding-agent assignment
      │
      ▼
Local audit trail
  ├── task created
  ├── run created
  ├── events appended
  └── state = delegated
      │
      ▼
GitHub coding agent executes in GitHub environment
      │
      ▼
GitHub opens PR in target repository
```

#### 2. Chat -> local pipeline (fallback)

```text
Remote Operator
      │
      ▼
/chat session
      │
      ▼
Task creation + Redis enqueue
      │
      ▼
Local worker executes TaskPipeline
      │
      ▼
Branch creation + file writes + draft PR
```

#### 3. GitHub comment -> local pipeline

```text
GitHub issue comment in control repository
      │
      ▼
Worker polling loop
      │ detects @agent / repo= / /repo / /approve / /reject
      ▼
Task normalization -> PostgreSQL + Redis
      │
      ▼
Local worker executes TaskPipeline
```

---

## Core Concepts

### Chat Control Plane

The `/chat` UI is the main operator surface.

It supports:

- persistent sessions
- ordered message history
- target repository selection
- base branch override
- custom agent name
- manager-model selection
- clarification before dispatch
- run history per session

### Manager Model

The selected model in `/chat` is used by Agentic Coder for:

- clarification
- request shaping
- dispatch planning
- issue body generation

Model cost labels shown in the UI:

- `0x`: no premium Copilot token charge for the manager model
- `1x`: premium Copilot token charge for the manager model
- `local`: runs through Ollama locally

These labels apply to the manager model only. GitHub coding-agent execution still carries GitHub-side cost for Actions minutes and Copilot premium requests.

### Execution Backend

Configured in `agentic.yaml`:

```yaml
chat:
  execution_backend: github_coding_agent   # github_coding_agent | local_pipeline
  auto_prepare: true
```

- `github_coding_agent` is the default and recommended mode
- `local_pipeline` is the fallback self-hosted execution mode

### Delegated State

A delegated task is a task that Agentic Coder prepared and handed off to GitHub coding agent.

`delegated` means:

- a local audit record exists
- the request was successfully dispatched
- implementation is now happening outside the local worker

---

## Data Model

### Task States

```text
RECEIVED -> NORMALIZED -> INDEXED -> PLANNED -> AWAITING_APPROVAL -> READY -> RUNNING -> SUCCEEDED
                                          \ (autonomous mode)    /               \ FAILED

Chat dispatch path:
RECEIVED -> NORMALIZED -> PLANNED -> RUNNING -> DELEGATED
```

| State | Meaning |
|---|---|
| `received` | Task created from webhook, poll, or chat dispatch audit |
| `normalized` | Payload extracted and validated |
| `indexed` | Local pipeline context indexed |
| `planned` | Local plan or dispatch plan prepared |
| `awaiting_approval` | Local gated flow waiting for human `/approve` |
| `ready` | Approved or autonomous local task |
| `running` | Local worker or dispatch audit currently executing |
| `delegated` | Handed off to GitHub coding agent |
| `succeeded` | Local pipeline completed successfully |
| `failed` | Local pipeline or dispatch failed |
| `cancelled` | Rejected or manually cancelled |

### Database Tables

| Table | Purpose |
|---|---|
| `tasks` | One row per task with payload JSON and current state |
| `runs` | One row per execution or dispatch audit |
| `task_transitions` | Append-only state transition log |
| `run_events` | Event timeline for planning, dispatch, PR creation, and approvals |
| `poll_cursors` | Durable per-repo polling position |
| `chat_sessions` | Persistent operator sessions |
| `chat_messages` | Ordered transcript for each session |

---

## Local Pipeline

The local pipeline still exists and remains useful for fallback or self-hosted-only scenarios.

### Agents

- `PlannerAgent`
- `ContextRetrievalAgent`
- `KnowledgeGraphBuilder`
- `CodingAgent`
- `ReviewerAgent`
- `SecurityAgent`
- `TestAgent`
- `PullRequestGenerator`

### Current semantics

- the pipeline can produce proposed file changes when the proposal includes concrete full-file content
- the local PR flow still writes `.agentic/runs/<run_id>.md`
- local patch application is constrained by proposal quality and available retrieved context

---

## GitHub Dispatch Integration

### Required credentials

Two different GitHub credential paths are involved:

1. `GitHub App credentials`
   - used for repo integration, polling, comments, branch work, and local PR operations
2. `GITHUB_AGENT_USER_TOKEN`
   - used for issue creation that assigns GitHub coding agent in the target repository

### Dispatch behavior

When the chat backend is `github_coding_agent` and the request is ready:

1. Agentic Coder prepares the issue title, issue body, and custom instructions.
2. It creates a GitHub issue in the target repository.
3. It assigns `copilot-swe-agent[bot]`.
4. It records local events such as:
   - `dispatch_prepared`
   - `github_agent_issue_created`
   - `github_agent_dispatched`
5. It marks the task `delegated`.

### Installation context separation

Local tasks may carry both:

- `source_installation_id`
- `target_installation_id`

This prevents cross-repo confusion when the control repository and target repository differ.

---

## Trigger Modes

### Polling (default)

- worker polls `system.control_repository`
- no public webhook endpoint required
- bot comments are filtered out
- durable cursor stored in `poll_cursors`

### Webhook

- API receives `POST /github/webhook`
- validates `X-Hub-Signature-256`
- immediately enqueues local tasks

### Hybrid

- polling and webhook enabled together

---

## Command Syntax (GitHub Comments)

### Triggering a local task

```text
@agent repo=owner/reponame
Description of the change to make
```

```text
/repo owner/reponame
Description of the change to make
```

### Approving a local gated task

```text
/approve
```

```text
/approve <task_id>
/approve task=<task_id>
```

### Rejecting a local gated task

```text
/reject reason goes here
/reject <task_id> reason goes here
```

These approval commands apply to the local worker pipeline. They are not required for the default GitHub-agent chat dispatch flow.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/readyz` | Readiness probe |
| `GET` | `/self-check` | Startup diagnostics alias |
| `GET` | `/startup/self-check` | Current startup self-check result |
| `POST` | `/startup/self-check/run` | Re-run GitHub self-check |
| `GET` | `/polling/status` | Polling configuration and cursor state |
| `POST` | `/github/webhook` | GitHub webhook receiver |
| `POST` | `/tasks` | Create a task manually |
| `GET` | `/tasks` | List recent tasks |
| `GET` | `/tasks/{task_id}` | Task detail |
| `POST` | `/tasks/{task_id}/approve` | Approve a local gated task |
| `POST` | `/tasks/{task_id}/reject` | Reject a local gated task |
| `GET` | `/runs/{run_id}` | Run detail |
| `GET` | `/runs/{run_id}/events` | Run event timeline |
| `POST` | `/runs/{run_id}/pull-request` | Manually trigger local PR creation |
| `GET` | `/dashboard` | HTML dashboard UI |
| `GET` | `/dashboard/data` | Dashboard JSON data |
| `GET` | `/chat` | Chat control-plane UI |
| `POST` | `/chat/sessions` | Create a chat session |
| `GET` | `/chat/sessions` | List sessions |
| `GET` | `/chat/sessions/{session_id}` | Session detail |
| `GET` | `/chat/sessions/{session_id}/messages` | Session transcript |
| `POST` | `/chat/sessions/{session_id}/messages` | Append message |
| `GET` | `/chat/sessions/{session_id}/runs` | Local audit trail for session |
| `POST` | `/chat/sessions/{session_id}/prepare` | Clarify and prepare dispatch |
| `POST` | `/chat/sessions/{session_id}/execute` | Dispatch to GitHub agent or queue local pipeline |

---

## Operations Dashboard

Available at `http://localhost:8080/dashboard`.

Displays:

- autonomy mode
- polling status
- model runtime
- chat execution backend
- task state
- run status
- delegated GitHub issue link when applicable
- PR link when applicable

---

## Security Controls

- `allow_any_target_repository: false` keeps target repos allowlisted by default
- GitHub bot comments are ignored during polling to prevent feedback loops
- webhook requests are HMAC-validated with `GITHUB_WEBHOOK_SECRET`
- local local-pipeline path still checks for cross-repo path mismatch before PR creation
- admin-protected routes can be gated with `API_ADMIN_TOKEN`

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `development` | Environment name |
| `API_ADMIN_TOKEN` | — | Optional API/UI admin token |
| `GITHUB_APP_ID` | — | GitHub App numeric ID |
| `GITHUB_WEBHOOK_SECRET` | — | HMAC secret for webhook verification |
| `GITHUB_PRIVATE_KEY` | — | Inline PEM private key |
| `GITHUB_PRIVATE_KEY_PATH` | — | Path to PEM private key |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | GitHub API base |
| `GITHUB_AGENT_USER_TOKEN` | — | User token used for GitHub coding-agent issue dispatch |
| `GITHUB_MODELS_API_KEY` | — | GitHub Models API key for manager-model calls |
| `GITHUB_MODELS_BASE_URL` | `https://models.inference.ai.azure.com` | GitHub Models endpoint |
| `GITHUB_MODELS_CHAT_MODEL` | `gpt-4.1-mini` | Default GitHub manager model |
| `DATABASE_URL` | `postgresql+psycopg://...` | PostgreSQL connection string |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string |
| `QUEUE_NAME` | `agentic:tasks` | Redis queue key |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama endpoint |
| `OLLAMA_CHAT_MODEL` | `llama3.1:8b` | Default local manager model |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | `40.0` | Per-request model timeout |

---

## Development

### Quick Start

```bash
make bootstrap
make qa
```

### Validation

```bash
make test-regression
make smoke
make qa
```

### Migrations

```bash
make migrate
alembic revision --autogenerate -m "description"
```

### Product guidance

The highest-leverage future work is now in:

- stronger clarification and planning loops
- richer product memory and cross-session context
- better GitHub-agent dispatch tracking and status sync
- multi-repo orchestration
- policy-driven routing across GitHub agent, local models, and local execution

The lowest-leverage future work is trying to outbuild GitHub's implementation engine inside this repo.
