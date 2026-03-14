# Usage Guide: GitHub Comment and Chat Workflows

## GitHub comment workflow

1. Open an issue in the control repository: `italnstallion789/agentic_coder`.
2. Add a request comment with an agent command, for example:

   ```text
   @agent repo=italnstallion789/predictiv
   Add a health-check endpoint and update the tests.
   ```

3. The worker polls the control repository, normalizes the request, stores the task in PostgreSQL, and enqueues it through Redis.
4. Track progress through:
   - `GET /tasks`
   - `GET /tasks/{task_id}`
   - `GET /tasks/{task_id}/timeline`
   - `GET /dashboard`

## Remote approval workflow

When `autonomy.mode` is `gated`, the run pauses at `awaiting_approval` after planning, proposal generation, review, security scan, and PR draft generation.

Approve from the same control-repository issue thread with either:

```text
/approve
```

or:

```text
/approve <task_id>
```

Reject with either:

```text
/reject reason here
```

or:

```text
/reject <task_id> reason here
```

After approval, the worker opens a draft PR in the target repository from an `agentic/<run>` branch.

## Chat control plane workflow

The app also supports remote intake from `http://localhost:8080/chat`.

1. Open `/chat`.
2. If `API_ADMIN_TOKEN` is configured, enter it when prompted by the UI.
3. Create a session for an allowed target repository and choose the execution model from the dropdown.
4. Add one or more messages describing the work.
5. In `gated` mode, set an approval issue number on the session or execution request.
6. Execute the session to enqueue it as a normal task.

The model dropdown is populated from the live backend runtime:

- `0x` means no premium Copilot token charge
- `1x` means premium Copilot token charge
- `local` means the configured Ollama model

Chat sessions are persisted in PostgreSQL and linked back to any tasks they create.

## What the current PR contains

The current implementation always writes a run artifact and may also write model-generated source edits into the target repository.

- The branch contains a run artifact at `.agentic/runs/<run_id>.md`
- If the coding proposal includes concrete `file_changes`, those files are committed into the PR branch before the artifact
- The draft PR title/body come from the pipeline output
- The proposal, target files, review result, and test plan are visible through task/run metadata

This means the system can now open real file-changing PRs, but patch quality still depends on the coding proposal producing complete file contents from the retrieved context.

## Operational endpoints

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
