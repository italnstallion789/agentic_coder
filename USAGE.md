# Usage Guide: Agentic Coder Polling Workflow

## Triggering Tasks via GitHub Comments

1. **Go to your control repository:**
   - Navigate to `italnstallion789/agentic_coder` on GitHub.

2. **Open an issue or pull request:**
   - You can use an existing issue/PR or create a new one.

3. **Add a comment with an agent command:**
   - To trigger the agent, include `@agent` or `repo=...` in your comment. Example:

     ```
     @agent repo=italnstallion789/predictiv
     Please implement a function to add two numbers in the target repo.
     ```

   - Or:

     ```
     /repo italnstallion789/predictiv
     Add a README badge for build status.
     ```

4. **Agent polling and task creation:**
   - The agent polls for new comments in the control repo.
   - When it detects a valid command, it creates a task for the specified target repository.

5. **Monitor task status:**
   - Use API endpoints or logs to track task creation and progress.

## Approving Gated Tasks Remotely

When autonomy mode is `gated`, tasks stop at `awaiting_approval`. You can approve directly from GitHub comments.

1. **On the same issue thread, add an approval comment:**

   ```
   /approve
   ```

2. **Optional: approve a specific task ID explicitly:**

   ```
   /approve <task_id>
   ```

   or:

   ```
   /approve task=<task_id>
   ```

3. **What happens next:**
   - Poller sees the approval command.
   - Matching task transitions from `awaiting_approval` to `ready`.
   - Agent opens a draft PR in the target repository from a feature branch.
   - Task transitions to `succeeded` when PR creation completes.
   - You can verify via `GET /tasks` and task timeline endpoints.

4. **Reject a task remotely:**

   ```
   /reject <task_id> reason here
   ```

   or:

   ```
   /reject reason here
   ```

   This marks the matching task as `cancelled` with rejection details in timeline metadata.

## Branching Behavior

- When code changes are made, the agent creates a new feature branch in the target repository (not in the develop/main branch).
- The branch name is typically `agentic/<run_id>` or similar.
- A pull request is then opened from the feature branch to the default branch (e.g., `main` or `master`).
- This ensures all changes are reviewed and merged via PR, keeping the develop/main branch clean.

## Summary
- Use issue/PR comments in your control repo to trigger agentic tasks.
- Specify the target repo with `repo=...` or `/repo ...`.
- The agent creates feature branches and PRs for all code changes.
- Monitor progress via API or logs.

## Operations Dashboard

- Open `http://localhost:8080/dashboard` for a human-readable UI.
- Dashboard data endpoint: `GET /dashboard/data`.
- Dashboard shows, per task:
   - request title/body and sender
   - source and target repository
   - current task status
   - run id and run status
   - PR draft title and opened PR link (when available)
   - polling summary (mode and last poll timestamp)

## Required GitHub App Permissions

For remote status updates and approval comments, your GitHub App must include:

- Metadata: Read
- Contents: Read & Write
- Pull requests: Read & Write
- Issues: Read & Write

For more details, see the README or API documentation.
