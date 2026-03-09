import asyncio
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


def resolve_workspace_root(policy: object, repository: str) -> Path:
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

    return Path.cwd()


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
    return "@agent" in lowered or lowered.strip().startswith("/repo") or "repo=" in lowered


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
        latest_since = str(since) if since else None
        latest_comment_id = last_comment_id

        for comment in comments:
            updated_at = str(comment.get("updated_at") or "")
            comment_id = int(comment.get("id") or 0)

            if latest_since and updated_at == latest_since and comment_id <= latest_comment_id:
                continue

            latest_since = updated_at or latest_since
            latest_comment_id = comment_id

            normalized = github.normalize_polled_issue_comment(
                control_repository,
                installation_id,
                comment,
            )
            if not should_accept_body_as_command(normalized.body):
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
                    "title": normalized.title,
                    "body": normalized.body,
                    "issue_number": normalized.issue_number,
                    "sender": normalized.sender,
                    "polling_comment_id": comment_id,
                    "polling_updated_at": updated_at,
                },
            )
            queue.enqueue(task.task_id)
            created += 1

        next_cursor = dict(cursor)
        if latest_since:
            next_cursor["since"] = latest_since
            next_cursor["last_comment_id"] = latest_comment_id
        next_cursor["control_repository"] = control_repository
        next_cursor["last_polled_at"] = polled_at
        next_cursor["last_seen_count"] = len(comments)
        next_cursor["last_enqueued_count"] = created

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
                with session_factory() as poll_session:
                    poll_repo = TaskRepository(poll_session)
                    created = poll_control_repository_once(policy, poll_repo, queue)
                last_poll_time = now
                logger.info("worker.poll_cycle", created=created)

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
            run_id = process_task(
                repo,
                task.task_id,
                title=task.title,
                autonomy_mode=policy.autonomy.mode,
                workspace_root=workspace_root,
            )

        logger.info(
            "worker.task_processed",
            task_id=queued.task_id,
            run_id=run_id,
            workspace_root=str(workspace_root),
        )


if __name__ == "__main__":
    main()
