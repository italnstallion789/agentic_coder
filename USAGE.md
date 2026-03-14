# Usage Guide

## Recommended workflow: remote chat to GitHub agent

This is the primary way to use Agentic Coder now.

1. Open `http://<host>:8080/chat`.
2. If `API_ADMIN_TOKEN` is configured, enter it when prompted.
3. Create a session with:
   - a short session title
   - an allowed target repository
   - an optional base branch override
   - an optional custom agent name
   - a manager model
4. Add one or more user messages describing the work.
5. Click `Analyze Request`.
6. If the session asks clarification questions, answer them in the chat and analyze again.
7. Click `Dispatch To GitHub Agent` when the request is ready.
8. Track the delegated issue and resulting PR in GitHub.
9. Use `/dashboard` to see the local audit trail and dispatched issue links.

## What the system actually does

When you click `Analyze Request`:

- the chat transcript is collected from PostgreSQL
- the selected manager model is used for clarification and dispatch planning
- the system decides whether the request is ready or needs more information
- the result is written back into the session transcript as an assistant message

When you click `Dispatch To GitHub Agent`:

- Agentic Coder prepares a GitHub issue body from the transcript and planning summary
- it creates a GitHub issue in the target repository
- it assigns GitHub coding agent to that issue
- it stores a local task and run so you keep a private audit trail
- the local task moves to `delegated`

The coding work itself then happens through GitHub's coding-agent workflow, not the local worker.

## Model selection

The chat UI exposes a live model catalog from the backend runtime.

- `0x`: manager model does not consume premium Copilot token charge
- `1x`: manager model consumes premium Copilot token charge
- `local`: manager model runs through Ollama locally

These labels only describe the manager model used by Agentic Coder for planning and clarification.

If you dispatch to GitHub coding agent, GitHub execution still uses its own billing model.

## Required setup for GitHub-agent dispatch

You need all of the following:

- GitHub App credentials configured for repo access
- `GITHUB_AGENT_USER_TOKEN` configured in the runtime environment
- target repository present in `allowed_target_repositories`
- GitHub coding agent available for the target repository

Recommended `.env` items:

```dotenv
GITHUB_APP_ID=...
GITHUB_PRIVATE_KEY_PATH=/run/secrets/github_app_private_key.pem
GITHUB_AGENT_USER_TOKEN=ghu_...
GITHUB_MODELS_API_KEY=ghp_...
API_ADMIN_TOKEN=replace-me-if-you-want-ui-protection
```

## Fallback workflow: local pipeline

If you set this in `agentic.yaml`:

```yaml
chat:
  execution_backend: local_pipeline
```

then `/chat` will queue a normal local task instead of delegating to GitHub coding agent.

That path is still useful when:

- you want local-only execution
- you do not want to dispatch into GitHub's coding-agent environment
- you are testing the local worker pipeline

### Gated mode for local pipeline

If `autonomy.mode` is `gated` and `chat.execution_backend` is `local_pipeline`:

- set `approval_issue_number` on the chat session or execute payload
- the worker will post approval comments to the control repository issue
- approve with `/approve` or reject with `/reject`

## GitHub comment workflow

The control repository flow still works.

Example request comment:

```text
@agent repo=owner/repo
Improve onboarding completion and add regression coverage.
```

Approve in gated mode with:

```text
/approve
```

Reject with:

```text
/reject needs a smaller scope
```

This workflow is now secondary to the chat control plane, but it remains available.

## How to operate it remotely

A practical remote setup looks like this:

1. Run the stack on a machine you control.
2. Expose that machine through Tailscale.
3. Visit `/chat` and `/dashboard` from your phone.
4. Use `/chat` as the command surface.
5. Let GitHub handle implementation, PR creation, review, and merge on the repository side.

That gives you a private control plane without rebuilding GitHub's coding worker.

## Useful endpoints

- `GET /healthz`
- `GET /readyz`
- `GET /startup/self-check`
- `GET /polling/status`
- `GET /dashboard`
- `GET /chat`

## Validation commands

- `make test-regression`
- `make smoke`
- `make qa`
