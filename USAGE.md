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

For more details, see the README or API documentation.
