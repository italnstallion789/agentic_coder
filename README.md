# Agentic Coder

Agentic Coder is a private AI engineering control plane.

It gives you a remote `/chat` endpoint you control, lets you capture work from your phone or browser, clarifies the request, and then dispatches implementation to GitHub Copilot coding agent by default. It also keeps a local pipeline backend as a fallback for self-hosted execution.

## Product direction

This repository is no longer best understood as "a replacement for GitHub's coding agent." The stronger design is:

- `Agentic Coder` as the manager and private command center
- `GitHub Copilot coding agent` as the primary implementation worker
- `local_pipeline` as a fallback backend for local-only execution

That gives you:

- a private endpoint over your own network or Tailscale
- persistent chat memory, sessions, runs, and audit history
- repository allowlists and policy control
- model selection for clarification/planning
- a clean handoff into GitHub-native issue, PR, and review flows

## Current capabilities

- FastAPI control plane with health, readiness, self-check, dashboard, and chat endpoints
- PostgreSQL-backed tasks, runs, run events, transitions, poll cursors, chat sessions, and chat messages
- Chat-first intake at `/chat`
- Clarification and dispatch planning before execution
- Default chat backend: `github_coding_agent`
- Fallback chat backend: `local_pipeline`
- GitHub App integration for repo access, polling, comments, branches, and PRs
- GitHub-agent dispatch audit trail recorded locally as `delegated` tasks
- GitHub comment workflow for control-repo task intake and remote approvals
- Regression, smoke, and CI coverage

## How it works

### Default path: chat -> GitHub coding agent

1. You open `/chat`.
2. Create a session for an allowed target repository.
3. Choose a manager model.
4. Add one or more chat messages describing the work.
5. Click `Analyze Request`.
6. The chat manager either:
   - marks the request ready, or
   - asks clarification questions and stores them in the session transcript.
7. Click `Dispatch To GitHub Agent` when the request is ready.
8. Agentic Coder creates a GitHub issue in the target repository, assigns GitHub coding agent, and records a local task/run audit trail in state `delegated`.
9. GitHub's coding agent performs the implementation work in GitHub's environment and opens the PR in the target repository.

### Fallback path: chat -> local pipeline

If `chat.execution_backend` is set to `local_pipeline`, the chat session creates a normal local task, queues it through Redis, and the local worker executes the current planning/coding/review/PR pipeline.

That path still exists, but it is now the fallback mode rather than the default product direction.

## Chat operating model

The `/chat` UI is the main operator surface.

Session inputs:

- `Session Title`
- `Target Repository`
- `Base Branch`
- `Custom Agent (optional)`
- `Manager Model`
- `Operator Identity`

Session actions:

- `Create Session`
- `Analyze Request`
- `Dispatch To GitHub Agent`
- `Refresh`

The manager model is used for clarification and dispatch planning.

- GitHub-backed models are labeled with Copilot token cost tiers:
  - `0x` means no premium Copilot token charge for that model choice
  - `1x` means premium Copilot token charge for that model choice
- `local` means the planning model runs through Ollama locally

Important: those labels describe the manager model used by Agentic Coder for planning. When you dispatch to GitHub coding agent, GitHub's execution still carries its own cost model, including GitHub Actions minutes and Copilot premium requests.

## Approvals and control

### GitHub-agent backend

For the default `github_coding_agent` backend:

- clarification happens in `/chat` before dispatch
- after dispatch, the implementation lifecycle is GitHub-native
- code review, iteration, and merge happen in GitHub on the dispatched issue and resulting PR

### Local pipeline backend

For `local_pipeline` in `gated` mode:

- you still approve remotely through GitHub issue comments
- chat execution requires an `approval_issue_number`
- the worker posts approval and status comments to the control repository issue
- `/approve` and `/reject` comments drive state transitions

## Development workflow

This project is designed to run container-first.

### Quick start

- `make bootstrap`
- `make qa`

Useful commands:

- `make up`
- `make down`
- `make logs`
- `make migrate`
- `make test`
- `make lint`
- `make test-regression`
- `make smoke`
- `make qa`
- `make shell`

The API is exposed on host port `8080`.

## Key configuration

Primary product behavior is controlled in [agentic.yaml](agentic.yaml).

Important sections:

```yaml
system:
  control_repository: owner/agentic_coder
  allow_any_target_repository: false
  allowed_target_repositories:
    - owner/predictiv

autonomy:
  mode: gated

chat:
  execution_backend: github_coding_agent   # github_coding_agent | local_pipeline
  auto_prepare: true
```

### Required environment variables

For the default GitHub-agent flow:

- `GITHUB_APP_ID`
- `GITHUB_PRIVATE_KEY` or `GITHUB_PRIVATE_KEY_PATH`
- `GITHUB_AGENT_USER_TOKEN`
- `DATABASE_URL`
- `REDIS_URL`

Optional but common:

- `API_ADMIN_TOKEN`
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_MODELS_API_KEY`
- `GITHUB_MODELS_CHAT_MODEL`
- `OLLAMA_BASE_URL`
- `OLLAMA_CHAT_MODEL`

`GITHUB_AGENT_USER_TOKEN` is the credential used to create the dispatched issue that assigns GitHub coding agent in the target repository.

## Operational endpoints

- `GET /healthz`
- `GET /readyz`
- `GET /self-check`
- `GET /startup/self-check`
- `GET /polling/status`
- `GET /dashboard`
- `GET /dashboard/data`
- `GET /chat`

Chat API:

- `POST /chat/sessions`
- `GET /chat/sessions`
- `GET /chat/sessions/{session_id}`
- `GET /chat/sessions/{session_id}/messages`
- `POST /chat/sessions/{session_id}/messages`
- `GET /chat/sessions/{session_id}/runs`
- `POST /chat/sessions/{session_id}/prepare`
- `POST /chat/sessions/{session_id}/execute`

## What the dashboard shows

The dashboard tracks both local and delegated work.

For delegated GitHub-agent runs it now shows:

- task state `delegated`
- local run metadata
- dispatched issue link
- PR link when available from the tracked run history

## What remains true

- GitHub issue comment polling still works and is useful for control-repo workflows
- the local worker and PR pipeline still exist
- this repo still supports real file-changing PRs in the local pipeline when the proposal contains full file contents

The difference is that the default product posture is now orchestration and control, not rebuilding GitHub's coding worker.
