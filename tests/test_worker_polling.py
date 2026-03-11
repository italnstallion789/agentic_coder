from agentic_coder.domain.tasks import TaskRecord, TaskState
from agentic_coder.worker import (
    build_approval_comment,
    extract_approval_task_id,
    extract_rejection_reason,
    extract_rejection_task_id,
    find_latest_awaiting_approval_task,
    is_approval_command,
    is_rejection_command,
    is_target_repository_allowed,
    should_accept_body_as_command,
)


class _RepoWithRecent:
    def __init__(self, tasks: list[TaskRecord]) -> None:
        self._tasks = tasks

    def list_recent(self, limit: int = 50) -> list[TaskRecord]:
        _ = limit
        return self._tasks


class _Policy:
    class system:  # noqa: N801
        allow_any_target_repository = False
        allowed_target_repositories = ["predictiv"]


def test_should_accept_body_as_command() -> None:
    assert should_accept_body_as_command("@agent implement this") is True
    assert should_accept_body_as_command("repo=predictiv do it") is True
    assert should_accept_body_as_command("/approve") is True
    assert should_accept_body_as_command("/approval") is True
    assert should_accept_body_as_command("/reject bad plan") is True
    assert should_accept_body_as_command("regular comment") is False


def test_target_repository_allowlist_supports_repo_name_match() -> None:
    assert is_target_repository_allowed(_Policy(), "acme/predictiv") is True
    assert is_target_repository_allowed(_Policy(), "acme/control") is False


def test_approval_command_parsing() -> None:
    assert is_approval_command("/approve") is True
    assert is_approval_command("/approval") is True
    assert is_approval_command("@agent approve") is True
    assert is_approval_command("please do this") is False

    assert (
        extract_approval_task_id("/approve 123e4567-e89b-12d3-a456-426614174000")
        == "123e4567-e89b-12d3-a456-426614174000"
    )
    assert (
        extract_approval_task_id("/approve task=123e4567-e89b-12d3-a456-426614174000")
        == "123e4567-e89b-12d3-a456-426614174000"
    )
    assert (
        extract_approval_task_id("/approval 123e4567-e89b-12d3-a456-426614174000")
        == "123e4567-e89b-12d3-a456-426614174000"
    )
    assert extract_approval_task_id("/approve") is None


def test_rejection_command_parsing() -> None:
    assert is_rejection_command("/reject") is True
    assert is_rejection_command("@agent reject") is True
    assert is_rejection_command("looks good") is False

    assert (
        extract_rejection_task_id("/reject 123e4567-e89b-12d3-a456-426614174000")
        == "123e4567-e89b-12d3-a456-426614174000"
    )
    assert (
        extract_rejection_task_id("/reject task=123e4567-e89b-12d3-a456-426614174000")
        == "123e4567-e89b-12d3-a456-426614174000"
    )
    assert extract_rejection_task_id("/reject not this") is None

    assert extract_rejection_reason("/reject too risky") == "too risky"
    assert (
        extract_rejection_reason(
            "/reject 123e4567-e89b-12d3-a456-426614174000 insufficient tests"
        )
        == "insufficient tests"
    )


def test_find_latest_awaiting_approval_task() -> None:
    tasks = [
        TaskRecord(
            task_id="task-1",
            title="one",
            payload={
                "source_repository": "acme/control",
                "repository": "acme/predictiv",
                "issue_number": 10,
            },
            state=TaskState.RECEIVED,
        ),
        TaskRecord(
            task_id="task-2",
            title="two",
            payload={
                "source_repository": "acme/control",
                "repository": "acme/predictiv",
                "issue_number": 10,
            },
            state=TaskState.AWAITING_APPROVAL,
        ),
    ]
    repo = _RepoWithRecent(tasks)

    resolved = find_latest_awaiting_approval_task(
        repo,  # type: ignore[arg-type]
        source_repository="acme/control",
        issue_number=10,
    )

    assert resolved == "task-2"


def test_build_approval_comment_contains_summary_and_commands() -> None:
    body = build_approval_comment(
        task_id="123e4567-e89b-12d3-a456-426614174000",
        run_id="run-1",
        repository="acme/predictiv",
        events=[
            {
                "event_type": "proposal_generated",
                "payload": {
                    "summary": "Implement cache layer",
                    "target_files": ["src/cache.py", "tests/test_cache.py"],
                },
            },
            {
                "event_type": "test_plan",
                "payload": {"commands": ["pytest -q", "ruff check src tests"]},
            },
            {
                "event_type": "pr_draft",
                "payload": {"title": "Add cache layer"},
            },
        ],
    )

    assert "Agent Proposal Ready for Approval" in body
    assert "Implement cache layer" in body
    assert "src/cache.py" in body
    assert "/approve 123e4567-e89b-12d3-a456-426614174000" in body
