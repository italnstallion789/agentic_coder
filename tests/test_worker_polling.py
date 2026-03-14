from datetime import UTC, datetime

from agentic_coder import worker as worker_module
from agentic_coder.domain.tasks import TaskRecord, TaskState
from agentic_coder.github_app.models import NormalizedGithubTask
from agentic_coder.worker import (
    build_approval_comment,
    create_pr_for_ready_task,
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


def test_create_pr_for_ready_task_sync_wrapper_uses_async_helper(monkeypatch) -> None:
    recorded: list[dict[str, object]] = []

    async def _fake_create_pr_for_ready_task_async(**kwargs) -> bool:  # noqa: ANN003
        recorded.append(kwargs)
        return True

    monkeypatch.setattr(
        worker_module,
        "create_pr_for_ready_task_async",
        _fake_create_pr_for_ready_task_async,
    )

    assert (
        create_pr_for_ready_task(
            repo=object(),  # type: ignore[arg-type]
            task_id="task-1",
            requested_by="alex",
            policy=object(),
        )
        is True
    )
    assert recorded[0]["task_id"] == "task-1"
    assert recorded[0]["requested_by"] == "alex"


class _PollingApprovalRepo:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.task = TaskRecord(
            task_id="123e4567-e89b-12d3-a456-426614174000",
            title="Queued task",
            payload={
                "source_repository": "acme/control",
                "repository": "acme/predictiv",
                "issue_number": 42,
                "installation_id": 77,
                "source_installation_id": 77,
                "target_installation_id": 88,
            },
            state=TaskState.AWAITING_APPROVAL,
            created_at=now,
            updated_at=now,
        )
        self.cursor: dict[str, object] | None = None
        self.transitions: list[tuple[str, str | None]] = []

    def get_poll_cursor(self, cursor_key: str) -> dict[str, object] | None:
        _ = cursor_key
        return None

    def upsert_poll_cursor(self, cursor_key: str, cursor: dict[str, object]) -> None:
        _ = cursor_key
        self.cursor = cursor

    def get_by_id(self, task_id: str) -> TaskRecord | None:
        if task_id != self.task.task_id:
            return None
        return self.task

    def update_state(self, task_id: str, state: TaskState, **kwargs) -> TaskRecord | None:  # noqa: ANN003
        if task_id != self.task.task_id:
            return None
        self.task.state = state
        self.transitions.append((state.value, kwargs.get("reason")))
        return self.task


class _PollingQueue:
    def enqueue(self, task_id: str) -> None:
        _ = task_id


class _PollingPolicy:
    class system:  # noqa: N801
        control_repository = "acme/control"
        allow_any_target_repository = False
        allowed_target_repositories = ["acme/predictiv", "predictiv"]

    class trigger:  # noqa: N801
        max_items_per_poll = 50


class _PollingGithub:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        _ = args, kwargs

    async def get_repository_installation(self, repository: str) -> dict[str, object]:
        _ = repository
        return {"id": 77}

    async def create_installation_token(self, installation_id: int) -> str:
        _ = installation_id
        return "token"

    async def list_issue_comments_since(
        self,
        repository: str,
        installation_token: str,
        *,
        since: str | None,
        per_page: int = 50,
    ) -> list[dict[str, object]]:
        _ = repository, installation_token, since, per_page
        return [
            {
                "id": 501,
                "updated_at": "2026-03-14T12:00:00Z",
                "body": "/approve 123e4567-e89b-12d3-a456-426614174000",
                "issue_url": "https://api.github.com/repos/acme/control/issues/42",
                "user": {"login": "alex", "type": "User"},
            }
        ]

    def normalize_polled_issue_comment(
        self,
        source_repository: str,
        installation_id: int,
        comment: dict[str, object],
    ) -> NormalizedGithubTask:
        _ = installation_id, comment
        return NormalizedGithubTask(
            event_name="issue_comment",
            source_repository=source_repository,
            target_repository="acme/predictiv",
            installation_id=77,
            title="Issue #42 comment from alex",
            body="/approve 123e4567-e89b-12d3-a456-426614174000",
            issue_number=42,
            sender="alex",
            raw_event={"polling": True},
        )


def test_poll_control_repository_once_uses_async_helpers_for_approval_flow(monkeypatch) -> None:
    repo = _PollingApprovalRepo()
    pr_calls: list[dict[str, object]] = []
    status_calls: list[dict[str, object]] = []

    async def _fake_create_pr_for_ready_task_async(**kwargs) -> bool:  # noqa: ANN003
        pr_calls.append(kwargs)
        return True

    async def _fake_publish_issue_status_update_for_task_async(**kwargs) -> None:  # noqa: ANN003
        status_calls.append(kwargs)

    def _sync_create_pr_should_not_run(**kwargs) -> bool:  # noqa: ANN003
        raise AssertionError("sync PR helper should not be used inside polling loop")

    def _sync_status_should_not_run(**kwargs) -> None:  # noqa: ANN003
        raise AssertionError("sync status helper should not be used inside polling loop")

    monkeypatch.setattr(worker_module, "GitHubAppService", _PollingGithub)
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: type(
            "_Settings",
            (),
            {
                "github_app_id": "1",
                "github_private_key": "test-key",
                "github_api_base_url": "https://api.github.com",
            },
        )(),
    )
    monkeypatch.setattr(
        worker_module,
        "create_pr_for_ready_task_async",
        _fake_create_pr_for_ready_task_async,
    )
    monkeypatch.setattr(worker_module, "create_pr_for_ready_task", _sync_create_pr_should_not_run)
    monkeypatch.setattr(
        worker_module,
        "publish_issue_status_update_for_task_async",
        _fake_publish_issue_status_update_for_task_async,
    )
    monkeypatch.setattr(
        worker_module,
        "publish_issue_status_update",
        _sync_status_should_not_run,
    )

    created = worker_module.poll_control_repository_once(
        _PollingPolicy(),
        repo,  # type: ignore[arg-type]
        _PollingQueue(),  # type: ignore[arg-type]
    )

    assert created == 0
    assert repo.transitions == [("ready", "github_comment_approved")]
    assert pr_calls[0]["task_id"] == repo.task.task_id
    assert status_calls[0]["status"] == "pr_opened"
    assert repo.cursor is not None
    assert repo.cursor["last_approved_count"] == 1
    assert repo.cursor["last_prs_created_count"] == 1


def test_poll_control_repository_once_uses_async_status_helper_for_rejections(
    monkeypatch,
) -> None:
    repo = _PollingApprovalRepo()
    status_calls: list[dict[str, object]] = []

    class _RejectingGithub(_PollingGithub):
        async def list_issue_comments_since(
            self,
            repository: str,
            installation_token: str,
            *,
            since: str | None,
            per_page: int = 50,
        ) -> list[dict[str, object]]:
            _ = repository, installation_token, since, per_page
            return [
                {
                    "id": 502,
                    "updated_at": "2026-03-14T12:01:00Z",
                    "body": "/reject 123e4567-e89b-12d3-a456-426614174000 too risky",
                    "issue_url": "https://api.github.com/repos/acme/control/issues/42",
                    "user": {"login": "alex", "type": "User"},
                }
            ]

        def normalize_polled_issue_comment(
            self,
            source_repository: str,
            installation_id: int,
            comment: dict[str, object],
        ) -> NormalizedGithubTask:
            _ = installation_id, comment
            return NormalizedGithubTask(
                event_name="issue_comment",
                source_repository=source_repository,
                target_repository="acme/predictiv",
                installation_id=77,
                title="Issue #42 comment from alex",
                body="/reject 123e4567-e89b-12d3-a456-426614174000 too risky",
                issue_number=42,
                sender="alex",
                raw_event={"polling": True},
            )

    async def _fake_publish_issue_status_update_for_task_async(**kwargs) -> None:  # noqa: ANN003
        status_calls.append(kwargs)

    def _sync_status_should_not_run(**kwargs) -> None:  # noqa: ANN003
        raise AssertionError("sync status helper should not be used inside polling loop")

    monkeypatch.setattr(worker_module, "GitHubAppService", _RejectingGithub)
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: type(
            "_Settings",
            (),
            {
                "github_app_id": "1",
                "github_private_key": "test-key",
                "github_api_base_url": "https://api.github.com",
            },
        )(),
    )
    monkeypatch.setattr(
        worker_module,
        "publish_issue_status_update_for_task_async",
        _fake_publish_issue_status_update_for_task_async,
    )
    monkeypatch.setattr(
        worker_module,
        "publish_issue_status_update",
        _sync_status_should_not_run,
    )

    created = worker_module.poll_control_repository_once(
        _PollingPolicy(),
        repo,  # type: ignore[arg-type]
        _PollingQueue(),  # type: ignore[arg-type]
    )

    assert created == 0
    assert repo.transitions == [("cancelled", "github_comment_rejected")]
    assert status_calls[0]["status"] == "rejected"
    assert repo.cursor is not None
    assert repo.cursor["last_rejected_count"] == 1
