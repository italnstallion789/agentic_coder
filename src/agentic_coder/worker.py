import asyncio
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from structlog import get_logger

from agentic_coder.config import get_settings
from agentic_coder.db.repositories import TaskRepository
from agentic_coder.db.session import create_session_factory
from agentic_coder.domain.tasks import TaskState
from agentic_coder.github_app.service import GitHubAppService
from agentic_coder.logging import configure_logging
from agentic_coder.orchestration.pipeline import TaskPipeline
from agentic_coder.policy.loader import PolicyLoader
from agentic_coder.queue.redis_queue import RedisTaskQueue

configure_logging()
logger = get_logger(__name__)


def resolve_workspace_root(policy: object, repository: str) -> Path | None:
    mapping = policy.system.local_repository_paths
    direct = mapping.get(repository)
    if direct:
        path = Path(direct)
        if path.exists():
            return path

    repo_name = repository.split("/", maxsplit=1)[-1]
    by_name = mapping.get(repo_name)
    if by_name:
        path = Path(by_name)
        if path.exists():
            return path

    return None


def resolve_base_branch_for_repository(policy: object, repository: str) -> str:
    per_repo = policy.system.target_base_branches
    if repository in per_repo:
        return str(per_repo[repository])
    repo_name = repository.split("/", maxsplit=1)[-1]
    if repo_name in per_repo:
        return str(per_repo[repo_name])
    return str(policy.system.default_target_base_branch)


def is_bot_comment(comment: dict[str, object]) -> bool:
    user = comment.get("user") or {}
    login = str((user or {}).get("login") or "")
    user_type = str((user or {}).get("type") or "")
    return login.endswith("[bot]") or user_type.lower() == "bot"


def process_task(
    repo: TaskRepository,
    task_id: str,
    *,
    title: str,
    autonomy_mode: str,
    workspace_root: Path,
) -> str:
    pipeline = TaskPipeline(workspace_root=workspace_root)
    run_id = repo.create_run(task_id, worker_name="worker")
    try:
        repo.append_run_event(
            run_id,
            "task_received",
            {"task_id": task_id, "title": title},
        )
        repo.update_state(
            task_id,
            TaskState.NORMALIZED,
            run_id=run_id,
            reason="worker_normalize",
        )
        repo.update_state(
            task_id,
            TaskState.INDEXED,
            run_id=run_id,
            reason="worker_index",
        )
        repo.update_state(
            task_id,
            TaskState.PLANNED,
            run_id=run_id,
            reason="worker_plan",
        )

        repo.update_state(
            task_id,
            TaskState.RUNNING,
            run_id=run_id,
            reason="pipeline_start",
        )

        current_task = repo.get_by_id(task_id)
        if current_task is None:
            raise ValueError(f"Task not found during processing: {task_id}")
        result = pipeline.run(current_task)

        source_repo_name = str(current_task.payload.get("source_repository") or "").split(
            "/",
            maxsplit=1,
        )[-1]
        target_repo_name = str(current_task.payload.get("repository") or "").split(
            "/",
            maxsplit=1,
        )[-1]
        proposal_files = [str(path) for path in (result.proposal.target_files or [])]
        leaked_paths = [
            path
            for path in proposal_files
            if (
                source_repo_name
                and target_repo_name
                and source_repo_name != target_repo_name
                and (
                    source_repo_name in path
                    or path.startswith("src/agentic_coder/")
                    or path.startswith("agentic_coder/")
                )
            )
        ]
        if leaked_paths:
            repo.append_run_event(
                run_id,
                "proposal_target_mismatch",
                {
                    "source_repository": current_task.payload.get("source_repository"),
                    "target_repository": current_task.payload.get("repository"),
                    "leaked_paths": leaked_paths,
                },
            )
            repo.update_state(
                task_id,
                TaskState.FAILED,
                run_id=run_id,
                reason="proposal_target_mismatch",
                details={
                    "source_repository": current_task.payload.get("source_repository"),
                    "target_repository": current_task.payload.get("repository"),
                    "leaked_paths": leaked_paths,
                },
            )
            repo.complete_run(run_id, status="failed")
            return run_id

        repo.append_run_event(
            run_id,
            "plan_created",
            {
                "objective": result.plan.objective,
                "steps": result.plan.steps,
            },
        )
        repo.append_run_event(
            run_id,
            "context_indexed",
            {
                "indexed_files": result.indexed_files,
                "graph_summary": result.graph_summary,
            },
        )
        repo.append_run_event(
            run_id,
            "proposal_generated",
            {
                "summary": result.proposal.summary,
                "target_files": result.proposal.target_files,
            },
        )
        repo.append_run_event(
            run_id,
            "review_completed",
            {
                "approved": result.review.approved,
                "feedback": result.review.feedback,
            },
        )
        repo.append_run_event(
            run_id,
            "security_scan",
            {
                "passed": result.security.passed,
                "findings": result.security.findings,
            },
        )
        repo.append_run_event(
            run_id,
            "test_plan",
            {
                "commands": result.test_plan.commands,
            },
        )
        repo.append_run_event(
            run_id,
            "pr_draft",
            {
                "title": result.pr_draft.title,
                "body": result.pr_draft.body,
            },
        )
        repo.update_run_metadata(
            run_id,
            {
                "objective": result.plan.objective,
                "review_approved": result.review.approved,
                "security_passed": result.security.passed,
                "pr_title": result.pr_draft.title,
                "model_used": result.model_used,
            },
        )

        if not result.security.passed or not result.review.approved:
            repo.update_state(
                task_id,
                TaskState.FAILED,
                run_id=run_id,
                reason="pipeline_validation_failed",
                details={
                    "review_approved": result.review.approved,
                    "security_passed": result.security.passed,
                },
            )
            repo.complete_run(run_id, status="failed")
            return run_id

        if autonomy_mode == "gated":
            repo.update_state(
                task_id,
                TaskState.AWAITING_APPROVAL,
                run_id=run_id,
                reason="gated_mode_requires_approval",
            )
        else:
            repo.update_state(
                task_id,
                TaskState.READY,
                run_id=run_id,
                reason="autonomous_mode_ready",
            )
        repo.complete_run(run_id, status="succeeded")
        return run_id
    except Exception:
        repo.append_run_event(run_id, "worker_exception", {})
        repo.update_state(
            task_id,
            TaskState.FAILED,
            run_id=run_id,
            reason="worker_exception",
        )
        repo.complete_run(run_id, status="failed")
        raise


def should_poll(policy: object) -> bool:
    return policy.trigger.mode in {"polling", "hybrid"}


def should_accept_body_as_command(body: str) -> bool:
    lowered = body.lower()
    return (
        "@agent" in lowered
        or lowered.strip().startswith("/repo")
        or "repo=" in lowered
        or "/approve" in lowered
        or "/approval" in lowered
        or "/reject" in lowered
    )


def is_approval_command(body: str) -> bool:
    lowered = body.lower()
    return "/approve" in lowered or "/approval" in lowered or "@agent approve" in lowered


def extract_approval_task_id(body: str) -> str | None:
    match = re.search(
        r"(?:^|\s)/approv(?:e|al)(?:\s+task=|\s+)([0-9a-fA-F-]{36})(?:\s|$)",
        body,
    )
    if match:
        return match.group(1)
    return None


def is_rejection_command(body: str) -> bool:
    lowered = body.lower()
    return "/reject" in lowered or "@agent reject" in lowered


def extract_rejection_task_id(body: str) -> str | None:
    match = re.search(
        r"(?:^|\s)/reject(?:\s+task=|\s+)([0-9a-fA-F-]{36})(?:\s|$)",
        body,
    )
    if match:
        return match.group(1)
    return None


def extract_rejection_reason(body: str) -> str | None:
    match = re.search(
        r"(?:^|\s)/reject(?:\s+task=[0-9a-fA-F-]{36}|\s+[0-9a-fA-F-]{36})?\s*(.*)$",
        body,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    reason = match.group(1).strip()
    return reason or None


def find_latest_awaiting_approval_task(
    repo: TaskRepository,
    *,
    source_repository: str,
    issue_number: int | None,
) -> str | None:
    recent = repo.list_recent(limit=200)
    for task in recent:
        if task.state != TaskState.AWAITING_APPROVAL:
            continue
        payload = task.payload
        if str(payload.get("source_repository") or "") != source_repository:
            continue
        if int(payload.get("issue_number") or 0) != int(issue_number or 0):
            continue
        return task.task_id
    return None


def build_approval_comment(
    *,
    task_id: str,
    run_id: str,
    repository: str,
    events: list[dict[str, object]],
) -> str:
    proposal_event = next(
        (event for event in events if event.get("event_type") == "proposal_generated"),
        None,
    )
    pr_event = next((event for event in events if event.get("event_type") == "pr_draft"), None)
    test_plan_event = next(
        (event for event in events if event.get("event_type") == "test_plan"),
        None,
    )

    proposal_payload = dict(proposal_event.get("payload") or {}) if proposal_event else {}
    pr_payload = dict(pr_event.get("payload") or {}) if pr_event else {}
    test_payload = dict(test_plan_event.get("payload") or {}) if test_plan_event else {}

    summary = str(proposal_payload.get("summary") or "No proposal summary available.")
    target_files = proposal_payload.get("target_files") or []
    commands = test_payload.get("commands") or []
    pr_title = str(pr_payload.get("title") or "Draft PR")

    file_lines = "\n".join(f"- {path}" for path in target_files[:20])
    if not file_lines:
        file_lines = "- (none specified)"

    command_lines = "\n".join(f"- `{command}`" for command in commands[:10])
    if not command_lines:
        command_lines = "- (none specified)"

    return (
        "## Agent Proposal Ready for Approval\n\n"
        f"- Task ID: `{task_id}`\n"
        f"- Run ID: `{run_id}`\n"
        f"- Target Repository: `{repository}`\n"
        f"- Proposed PR Title: {pr_title}\n\n"
        "### Proposed Change Summary\n"
        f"{summary}\n\n"
        "### Target Files\n"
        f"{file_lines}\n\n"
        "### Planned Validation Commands\n"
        f"{command_lines}\n\n"
        "### Approval Commands\n"
        f"- `/approve {task_id}` to approve this task\n"
        "- `/approve` to approve the latest awaiting task in this issue\n"
        f"- `/reject {task_id} <reason>` to reject this task\n"
    )


async def create_pr_for_ready_task_async(
    *,
    repo: TaskRepository,
    task_id: str,
    requested_by: str,
    policy: object,
) -> bool:
    task = repo.get_by_id(task_id)
    if task is None:
        return False

    if task.state != TaskState.READY:
        return False

    payload = task.payload
    repository = str(payload.get("repository") or "")
    installation_id = int(
        payload.get("target_installation_id") or payload.get("installation_id") or 0
    )
    if not repository or installation_id <= 0:
        return False

    run = repo.get_latest_run_for_task(task_id)
    if run is None:
        return False
    run_id = str(run["run_id"])

    events = repo.list_run_events(run_id=run_id, limit=500)
    pr_event = next((event for event in events if event["event_type"] == "pr_draft"), None)
    if pr_event is None:
        return False

    pr_title = str((run.get("metadata") or {}).get("pr_title") or task.title)
    pr_body = str(pr_event["payload"].get("body") or "Automated change set")
    branch_name = f"agentic/{run_id[:8]}"

    settings = get_settings()
    if not settings.github_app_id or not settings.github_private_key:
        return False

    github = GitHubAppService(
        settings.github_app_id,
        settings.github_private_key,
        api_base_url=settings.github_api_base_url,
    )

    async def _open_pr() -> dict[str, object]:
        token = await github.create_installation_token(installation_id)
        base_branch = resolve_base_branch_for_repository(policy, repository)
        base_sha = await github.get_branch_head_sha(repository, base_branch, token)
        await github.create_branch(repository, branch_name, base_sha, token)

        artifact_path = f".agentic/runs/{run_id}.md"
        artifact_content = (
            f"# Run {run_id}\n\n"
            f"- Repository: {repository}\n"
            f"- Task ID: {task.task_id}\n"
            "- PR triggered by Agentic workflow\n"
            f"- Requested by: {requested_by}\n\n"
            f"## PR Title\n{pr_title}\n\n"
            f"## PR Body\n{pr_body}\n"
        )
        commit_result = await github.upsert_file(
            repository,
            token,
            branch=branch_name,
            path=artifact_path,
            message=f"agentic: add run artifact {run_id}",
            content=artifact_content,
        )
        pull_request = await github.create_pull_request(
            repository,
            token,
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=base_branch,
            draft=True,
        )
        return {
            "pull_request": pull_request,
            "branch_name": branch_name,
            "base_branch": base_branch,
            "commit_sha": ((commit_result.get("commit") or {}).get("sha") or ""),
        }

    repo.update_state(
        task_id,
        TaskState.RUNNING,
        run_id=run_id,
        reason="pr_creation_started",
        details={"requested_by": requested_by},
    )

    try:
        result = await _open_pr()
    except Exception as exc:
        repo.append_run_event(
            run_id,
            "pr_creation_failed",
            {"requested_by": requested_by, "error": str(exc)},
        )
        repo.update_state(
            task_id,
            TaskState.FAILED,
            run_id=run_id,
            reason="pr_creation_failed",
            details={"requested_by": requested_by, "error": str(exc)},
        )
        repo.complete_run(run_id, status="failed")
        return False

    pull_request = dict(result.get("pull_request") or {})
    repo.append_run_event(
        run_id,
        "approval_pr_created",
        {
            "requested_by": requested_by,
            "repository": repository,
            "branch_name": result.get("branch_name"),
            "base_branch": result.get("base_branch"),
            "pull_request_number": pull_request.get("number"),
            "pull_request_url": pull_request.get("html_url"),
            "commit_sha": result.get("commit_sha"),
        },
    )
    repo.update_state(
        task_id,
        TaskState.SUCCEEDED,
        run_id=run_id,
        reason="pr_opened",
        details={
            "requested_by": requested_by,
            "pull_request_number": pull_request.get("number"),
            "pull_request_url": pull_request.get("html_url"),
        },
    )
    repo.complete_run(run_id, status="succeeded")
    return True


def create_pr_for_ready_task(
    *,
    repo: TaskRepository,
    task_id: str,
    requested_by: str,
    policy: object,
) -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            create_pr_for_ready_task_async(
                repo=repo,
                task_id=task_id,
                requested_by=requested_by,
                policy=policy,
            )
        )
    raise RuntimeError(
        "create_pr_for_ready_task cannot run inside an active event loop; "
        "use create_pr_for_ready_task_async instead"
    )


def publish_approval_request_comment(
    *,
    repo: TaskRepository,
    task_id: str,
    run_id: str,
    task_payload: dict[str, object],
) -> None:
    source_repository = str(task_payload.get("source_repository") or "")
    issue_number = int(task_payload.get("issue_number") or 0)
    installation_id = int(
        task_payload.get("source_installation_id") or task_payload.get("installation_id") or 0
    )
    target_repository = str(task_payload.get("repository") or "")

    if not source_repository or issue_number <= 0 or installation_id <= 0:
        repo.append_run_event(
            run_id,
            "approval_comment_skipped",
            {
                "reason": "missing_issue_context",
                "source_repository": source_repository,
                "issue_number": issue_number,
                "installation_id": installation_id,
            },
        )
        return

    events = repo.list_run_events(run_id=run_id, limit=200)
    comment_body = build_approval_comment(
        task_id=task_id,
        run_id=run_id,
        repository=target_repository,
        events=events,
    )

    settings = get_settings()
    if not settings.github_app_id or not settings.github_private_key:
        return

    github = GitHubAppService(
        settings.github_app_id,
        settings.github_private_key,
        api_base_url=settings.github_api_base_url,
    )

    async def _publish() -> None:
        token = await github.create_installation_token(installation_id)
        await github.create_issue_comment(
            source_repository,
            issue_number,
            token,
            comment_body,
        )

    try:
        asyncio.run(_publish())
        logger.info(
            "worker.approval_comment_posted",
            task_id=task_id,
            run_id=run_id,
            source_repository=source_repository,
            issue_number=issue_number,
        )
    except Exception as exc:
        logger.warning(
            "worker.approval_comment_failed",
            task_id=task_id,
            run_id=run_id,
            error=str(exc),
        )


def _status_label(status: str) -> str:
    normalized = status.strip().lower().replace("_", "-")
    return f"agentic:{normalized}"


async def publish_issue_status_update_for_task_async(
    *,
    task_payload: dict[str, object],
    status: str,
    summary: str,
    details: dict[str, object] | None = None,
) -> None:
    source_repository = str(task_payload.get("source_repository") or "")
    issue_number = int(task_payload.get("issue_number") or 0)
    installation_id = int(
        task_payload.get("source_installation_id") or task_payload.get("installation_id") or 0
    )
    if not source_repository or issue_number <= 0 or installation_id <= 0:
        return

    settings = get_settings()
    if not settings.github_app_id or not settings.github_private_key:
        return

    body_lines = [
        "## Agentic Status Update",
        "",
        f"- Status: {status}",
        f"- Summary: {summary}",
    ]
    for key, value in (details or {}).items():
        body_lines.append(f"- {key}: {value}")
    body = "\n".join(body_lines)

    github = GitHubAppService(
        settings.github_app_id,
        settings.github_private_key,
        api_base_url=settings.github_api_base_url,
    )
    await publish_issue_status_update_async(
        github=github,
        source_repository=source_repository,
        issue_number=issue_number,
        installation_id=installation_id,
        status=status,
        body=body,
    )


def publish_issue_status_update(
    *,
    task_payload: dict[str, object],
    status: str,
    summary: str,
    details: dict[str, object] | None = None,
) -> None:
    publish_coro = publish_issue_status_update_for_task_async(
        task_payload=task_payload,
        status=status,
        summary=summary,
        details=details,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(publish_coro)
    else:
        loop.create_task(publish_coro)


async def publish_issue_status_update_async(
    *,
    github: GitHubAppService,
    source_repository: str,
    issue_number: int,
    installation_id: int,
    status: str,
    body: str,
) -> None:
    try:
        token = await github.create_installation_token(installation_id)
        await github.create_issue_comment(
            source_repository,
            issue_number,
            token,
            body,
        )
        await github.add_issue_labels(
            source_repository,
            issue_number,
            token,
            labels=[_status_label(status)],
        )
        logger.info(
            "worker.issue_status_published",
            source_repository=source_repository,
            issue_number=issue_number,
            status=status,
        )
    except Exception as exc:
        logger.warning(
            "worker.issue_status_publish_failed",
            source_repository=source_repository,
            issue_number=issue_number,
            status=status,
            error=str(exc),
        )


def is_target_repository_allowed(policy: object, repository: str) -> bool:
    if policy.system.allow_any_target_repository:
        return True
    if repository in policy.system.allowed_target_repositories:
        return True
    repo_name = repository.split("/", maxsplit=1)[-1]
    return repo_name in policy.system.allowed_target_repositories


def poll_control_repository_once(
    policy: object,
    repo: TaskRepository,
    queue: RedisTaskQueue,
) -> int:
    control_repository = policy.system.control_repository
    if not control_repository:
        return 0

    settings = get_settings()
    if not settings.github_app_id or not settings.github_private_key:
        return 0

    github = GitHubAppService(
        settings.github_app_id,
        settings.github_private_key,
        api_base_url=settings.github_api_base_url,
    )

    async def _poll() -> int:
        installation = await github.get_repository_installation(control_repository)
        installation_id = int(installation.get("id"))
        token = await github.create_installation_token(installation_id)

        cursor_key = f"github_poll:issue_comments:{control_repository}"
        cursor = repo.get_poll_cursor(cursor_key) or {}
        since = cursor.get("since")
        last_comment_id = int(cursor.get("last_comment_id") or 0)
        polled_at = datetime.now(UTC).isoformat()

        comments = await github.list_issue_comments_since(
            control_repository,
            token,
            since=str(since) if since else None,
            per_page=policy.trigger.max_items_per_poll,
        )

        created = 0
        approved = 0
        rejected = 0
        prs_created = 0
        latest_since = str(since) if since else None
        latest_comment_id = last_comment_id

        for comment in comments:
            updated_at = str(comment.get("updated_at") or "")
            comment_id = int(comment.get("id") or 0)

            if latest_since and updated_at == latest_since and comment_id <= latest_comment_id:
                continue

            latest_since = updated_at or latest_since
            latest_comment_id = comment_id

            if is_bot_comment(comment):
                continue

            normalized = github.normalize_polled_issue_comment(
                control_repository,
                installation_id,
                comment,
            )
            if not should_accept_body_as_command(normalized.body):
                continue

            if is_rejection_command(normalized.body):
                reject_task_id = extract_rejection_task_id(normalized.body)
                reason = extract_rejection_reason(normalized.body)
                if reject_task_id:
                    target_task = repo.get_by_id(reject_task_id)
                    target_task_id = (
                        target_task.task_id
                        if target_task and target_task.state == TaskState.AWAITING_APPROVAL
                        else None
                    )
                else:
                    target_task_id = find_latest_awaiting_approval_task(
                        repo,
                        source_repository=normalized.source_repository,
                        issue_number=normalized.issue_number,
                    )

                if target_task_id:
                    target_task = repo.get_by_id(target_task_id)
                    repo.update_state(
                        target_task_id,
                        TaskState.CANCELLED,
                        reason="github_comment_rejected",
                        details={
                            "rejected_by": normalized.sender,
                            "source_repository": normalized.source_repository,
                            "issue_number": normalized.issue_number,
                            "comment_id": comment_id,
                            "reason": reason,
                        },
                    )
                    if target_task is not None:
                        await publish_issue_status_update_for_task_async(
                            task_payload=target_task.payload,
                            status="rejected",
                            summary="Task was rejected from GitHub comment",
                            details={
                                "task_id": target_task_id,
                                "rejected_by": normalized.sender,
                                "reason": reason or "(none provided)",
                            },
                        )
                    rejected += 1
                continue

            if is_approval_command(normalized.body):
                approval_task_id = extract_approval_task_id(normalized.body)
                if approval_task_id:
                    task = repo.get_by_id(approval_task_id)
                    if task and task.state == TaskState.AWAITING_APPROVAL:
                        approved_task_id = task.task_id
                        repo.update_state(
                            task.task_id,
                            TaskState.READY,
                            reason="github_comment_approved",
                            details={
                                "approved_by": normalized.sender,
                                "source_repository": normalized.source_repository,
                                "issue_number": normalized.issue_number,
                                "comment_id": comment_id,
                            },
                        )
                        approved += 1
                        try:
                            if await create_pr_for_ready_task_async(
                                repo=repo,
                                task_id=approved_task_id,
                                requested_by=normalized.sender,
                                policy=policy,
                            ):
                                prs_created += 1
                                await publish_issue_status_update_for_task_async(
                                    task_payload=task.payload,
                                    status="pr_opened",
                                    summary="Approval received and draft PR opened",
                                    details={
                                        "task_id": approved_task_id,
                                        "approved_by": normalized.sender,
                                    },
                                )
                            else:
                                await publish_issue_status_update_for_task_async(
                                    task_payload=task.payload,
                                    status="failed",
                                    summary="Approval received but PR creation failed",
                                    details={
                                        "task_id": approved_task_id,
                                        "approved_by": normalized.sender,
                                    },
                                )
                        except Exception as exc:
                            logger.warning(
                                "worker.approval_pr_failed",
                                task_id=approved_task_id,
                                error=str(exc),
                            )
                else:
                    latest_task_id = find_latest_awaiting_approval_task(
                        repo,
                        source_repository=normalized.source_repository,
                        issue_number=normalized.issue_number,
                    )
                    if latest_task_id:
                        latest_task = repo.get_by_id(latest_task_id)
                        repo.update_state(
                            latest_task_id,
                            TaskState.READY,
                            reason="github_comment_approved",
                            details={
                                "approved_by": normalized.sender,
                                "source_repository": normalized.source_repository,
                                "issue_number": normalized.issue_number,
                                "comment_id": comment_id,
                            },
                        )
                        approved += 1
                        try:
                            if await create_pr_for_ready_task_async(
                                repo=repo,
                                task_id=latest_task_id,
                                requested_by=normalized.sender,
                                policy=policy,
                            ):
                                prs_created += 1
                                if latest_task is not None:
                                    await publish_issue_status_update_for_task_async(
                                        task_payload=latest_task.payload,
                                        status="pr_opened",
                                        summary="Approval received and draft PR opened",
                                        details={
                                            "task_id": latest_task_id,
                                            "approved_by": normalized.sender,
                                        },
                                )
                            else:
                                if latest_task is not None:
                                    await publish_issue_status_update_for_task_async(
                                        task_payload=latest_task.payload,
                                        status="failed",
                                        summary="Approval received but PR creation failed",
                                        details={
                                            "task_id": latest_task_id,
                                            "approved_by": normalized.sender,
                                        },
                                    )
                        except Exception as exc:
                            logger.warning(
                                "worker.approval_pr_failed",
                                task_id=latest_task_id,
                                error=str(exc),
                            )
                continue

            if not is_target_repository_allowed(policy, normalized.target_repository):
                continue

            task = repo.create(
                title=normalized.title,
                payload={
                    "event_name": normalized.event_name,
                    "source_repository": normalized.source_repository,
                    "repository": normalized.target_repository,
                    "installation_id": normalized.installation_id,
                    "source_installation_id": normalized.installation_id,
                    "target_installation_id": normalized.installation_id,
                    "title": normalized.title,
                    "body": normalized.body,
                    "issue_number": normalized.issue_number,
                    "sender": normalized.sender,
                    "polling_comment_id": comment_id,
                    "polling_updated_at": updated_at,
                },
            )
            queue.enqueue(task.task_id)
            await publish_issue_status_update_for_task_async(
                task_payload=task.payload,
                status="queued",
                summary="Task request received and queued",
                details={
                    "task_id": task.task_id,
                    "target_repository": normalized.target_repository,
                },
            )
            created += 1

        next_cursor = dict(cursor)
        if latest_since:
            next_cursor["since"] = latest_since
            next_cursor["last_comment_id"] = latest_comment_id
        next_cursor["control_repository"] = control_repository
        next_cursor["last_polled_at"] = polled_at
        next_cursor["last_seen_count"] = len(comments)
        next_cursor["last_enqueued_count"] = created
        next_cursor["last_approved_count"] = approved
        next_cursor["last_rejected_count"] = rejected
        next_cursor["last_prs_created_count"] = prs_created

        repo.upsert_poll_cursor(cursor_key, next_cursor)

        return created

    return asyncio.run(_poll())


def main() -> None:
    policy = PolicyLoader(path=Path("agentic.yaml")).load()
    queue = RedisTaskQueue.from_settings()
    session_factory = create_session_factory()
    last_poll_time = 0.0

    logger.info("worker.started", autonomy_mode=policy.autonomy.mode)
    while True:
        if should_poll(policy):
            now = time.time()
            if now - last_poll_time >= policy.trigger.poll_interval_seconds:
                try:
                    with session_factory() as poll_session:
                        poll_repo = TaskRepository(poll_session)
                        created = poll_control_repository_once(policy, poll_repo, queue)
                    last_poll_time = now
                    logger.info("worker.poll_cycle", created=created)
                except Exception as exc:
                    last_poll_time = now  # back off; don't hammer on failure
                    logger.warning("worker.poll_error", error=str(exc))

        queued = queue.dequeue(timeout_seconds=5)
        if queued is None:
            logger.info("worker.idle")
            time.sleep(2)
            continue

        with session_factory() as session:
            repo = TaskRepository(session)
            task = repo.get_by_id(queued.task_id)
            if task is None:
                logger.warning("worker.task_missing", task_id=queued.task_id)
                continue
            target_repository = str(task.payload.get("repository") or "")
            workspace_root = resolve_workspace_root(policy, target_repository)
            if workspace_root is None:
                repo.update_state(
                    task.task_id,
                    TaskState.FAILED,
                    reason="target_workspace_unresolved",
                    details={"repository": target_repository},
                )
                logger.warning(
                    "worker.target_workspace_unresolved",
                    task_id=task.task_id,
                    repository=target_repository,
                )
                continue
            run_id = process_task(
                repo,
                task.task_id,
                title=task.title,
                autonomy_mode=policy.autonomy.mode,
                workspace_root=workspace_root,
            )
            updated_task = repo.get_by_id(task.task_id)
            if updated_task and updated_task.state == TaskState.AWAITING_APPROVAL:
                publish_approval_request_comment(
                    repo=repo,
                    task_id=updated_task.task_id,
                    run_id=run_id,
                    task_payload=updated_task.payload,
                )
                publish_issue_status_update(
                    task_payload=updated_task.payload,
                    status="awaiting_approval",
                    summary="Proposal is ready for review and approval",
                    details={
                        "task_id": updated_task.task_id,
                        "run_id": run_id,
                    },
                )
            elif (
                updated_task
                and updated_task.state == TaskState.READY
                and policy.autonomy.mode == "autonomous"
            ):
                created = create_pr_for_ready_task(
                    repo=repo,
                    task_id=updated_task.task_id,
                    requested_by="autonomous_mode",
                    policy=policy,
                )
                if created:
                    publish_issue_status_update(
                        task_payload=updated_task.payload,
                        status="pr_opened",
                        summary="Autonomous mode opened a draft PR",
                        details={
                            "task_id": updated_task.task_id,
                            "run_id": run_id,
                        },
                    )
                else:
                    publish_issue_status_update(
                        task_payload=updated_task.payload,
                        status="failed",
                        summary="Autonomous mode failed to open draft PR",
                        details={"task_id": updated_task.task_id, "run_id": run_id},
                    )

        logger.info(
            "worker.task_processed",
            task_id=queued.task_id,
            run_id=run_id,
            workspace_root=str(workspace_root),
        )


if __name__ == "__main__":
    main()
