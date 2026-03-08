import time
from pathlib import Path

from structlog import get_logger

from agentic_coder.db.repositories import TaskRepository
from agentic_coder.db.session import create_session_factory
from agentic_coder.domain.tasks import TaskState
from agentic_coder.logging import configure_logging
from agentic_coder.orchestration.pipeline import TaskPipeline
from agentic_coder.policy.loader import PolicyLoader
from agentic_coder.queue.redis_queue import RedisTaskQueue

configure_logging()
logger = get_logger(__name__)


def process_task(
    repo: TaskRepository,
    task_id: str,
    *,
    title: str,
    autonomy_mode: str,
    pipeline: TaskPipeline,
) -> str:
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


def main() -> None:
    policy = PolicyLoader(path=Path("agentic.yaml")).load()
    queue = RedisTaskQueue.from_settings()
    session_factory = create_session_factory()
    pipeline = TaskPipeline(workspace_root=Path.cwd())

    logger.info("worker.started", autonomy_mode=policy.autonomy.mode)
    while True:
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
            run_id = process_task(
                repo,
                task.task_id,
                title=task.title,
                autonomy_mode=policy.autonomy.mode,
                pipeline=pipeline,
            )

        logger.info("worker.task_processed", task_id=queued.task_id, run_id=run_id)


if __name__ == "__main__":
    main()
