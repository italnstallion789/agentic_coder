from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentic_coder import worker as worker_module
from agentic_coder.agents.coder import PatchProposal
from agentic_coder.agents.planner import PlanResult
from agentic_coder.agents.pr_generator import PullRequestDraft
from agentic_coder.agents.reviewer import ReviewResult
from agentic_coder.agents.security import SecurityResult
from agentic_coder.agents.tester import ExecutionTestPlan
from agentic_coder.domain.tasks import TaskRecord, TaskState
from agentic_coder.worker import process_task, resolve_workspace_root


@dataclass(slots=True)
class _PipelineResult:
    plan: PlanResult
    proposal: PatchProposal
    review: ReviewResult
    security: SecurityResult
    test_plan: ExecutionTestPlan
    pr_draft: PullRequestDraft
    graph_summary: dict[str, int]
    indexed_files: int
    model_used: str | None = None


class _PipelineStub:
    def run(self, task: TaskRecord) -> _PipelineResult:
        _ = task
        return _PipelineResult(
            plan=PlanResult(objective="Implement cache", steps=["step1"]),
            proposal=PatchProposal(summary="Do work", target_files=["src/x.py"]),
            review=ReviewResult(approved=True, feedback="ok"),
            security=SecurityResult(passed=True, findings=[]),
            test_plan=ExecutionTestPlan(commands=["pytest -q"]),
            pr_draft=PullRequestDraft(title="PR", body="Body"),
            graph_summary={"nodes": 3, "edges": 2},
            indexed_files=1,
        )


class _RepoStub:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.task = TaskRecord(
            task_id="task-1",
            title="Implement cache",
            payload={"title": "Implement cache", "body": ""},
            state=TaskState.RECEIVED,
            created_at=now,
            updated_at=now,
        )
        self.states: list[TaskState] = []
        self.events: list[str] = []
        self.metadata: dict[str, object] = {}
        self.completed_status: str | None = None

    def create_run(self, task_id: str, worker_name: str) -> str:
        _ = task_id, worker_name
        return "run-1"

    def append_run_event(self, run_id: str, event_type: str, payload: dict[str, object]) -> None:
        _ = run_id, payload
        self.events.append(event_type)

    def update_state(self, task_id: str, state: TaskState, **kwargs) -> TaskRecord | None:  # noqa: ANN003
        _ = task_id, kwargs
        self.task.state = state
        self.states.append(state)
        return self.task

    def get_by_id(self, task_id: str) -> TaskRecord | None:
        if task_id != "task-1":
            return None
        return self.task

    def update_run_metadata(self, run_id: str, metadata: dict[str, object]) -> None:
        _ = run_id
        self.metadata = metadata

    def complete_run(self, run_id: str, status: str) -> None:
        _ = run_id
        self.completed_status = status


def test_process_task_gated_mode_transitions_to_awaiting_approval() -> None:
    repo = _RepoStub()
    run_id = process_task(
        repo,
        "task-1",
        title="Implement cache",
        autonomy_mode="gated",
        workspace_root=Path.cwd(),
    )

    assert run_id == "run-1"
    assert repo.completed_status == "succeeded"
    assert repo.task.state == TaskState.AWAITING_APPROVAL
    assert "plan_created" in repo.events
    assert repo.metadata["review_approved"] is True


def test_resolve_workspace_root_by_repo_name() -> None:
    class _Policy:
        class system:  # noqa: N801
            local_repository_paths = {"predictiv": "/tmp"}

    resolved = resolve_workspace_root(_Policy(), "acme/predictiv")
    assert str(resolved) == "/tmp"


def test_process_task_fails_on_control_repo_path_leak(monkeypatch) -> None:
    class _LeakyPipeline:
        def __init__(self, workspace_root: Path) -> None:
            _ = workspace_root

        def run(self, task: TaskRecord) -> _PipelineResult:
            _ = task
            return _PipelineResult(
                plan=PlanResult(objective="Implement", steps=["step1"]),
                proposal=PatchProposal(
                    summary="Wrong repo paths",
                    target_files=["src/agentic_coder/worker.py"],
                ),
                review=ReviewResult(approved=True, feedback="ok"),
                security=SecurityResult(passed=True, findings=[]),
                test_plan=ExecutionTestPlan(commands=["pytest -q"]),
                pr_draft=PullRequestDraft(title="PR", body="Body"),
                graph_summary={"nodes": 1, "edges": 0},
                indexed_files=1,
            )

    class _LeakRepo(_RepoStub):
        def __init__(self) -> None:
            super().__init__()
            self.task.payload = {
                "title": "Implement cache",
                "body": "",
                "source_repository": "acme/agentic_coder",
                "repository": "acme/predictiv",
            }

    monkeypatch.setattr(worker_module, "TaskPipeline", _LeakyPipeline)
    repo = _LeakRepo()
    run_id = process_task(
        repo,
        "task-1",
        title="Implement cache",
        autonomy_mode="gated",
        workspace_root=Path.cwd(),
    )

    assert run_id == "run-1"
    assert repo.completed_status == "failed"
    assert repo.task.state == TaskState.FAILED
    assert "proposal_target_mismatch" in repo.events
