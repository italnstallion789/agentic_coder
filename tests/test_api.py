from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from agentic_coder.api.main import app
from agentic_coder.domain.tasks import TaskRecord, TaskState

client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["autonomy_mode"] == "gated"


@dataclass(slots=True)
class _FakeTask:
    task_id: str


class _FakeSession:
    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _FakeSessionFactory:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


class _FakeRepo:
    def __init__(self, session) -> None:  # noqa: ANN001
        self.session = session

    def create(self, title: str, payload: dict[str, object]) -> _FakeTask:
        assert title
        assert payload["repository"] == "acme/predictiv"
        assert payload["source_repository"] == "acme/control"
        return _FakeTask(task_id="task-123")


class _FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, task_id: str) -> None:
        self.enqueued.append(task_id)


def test_issue_comment_webhook_creates_task_and_queues(monkeypatch) -> None:
    from agentic_coder.api import main

    monkeypatch.setattr(main.WebhookVerifier, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main, "TaskRepository", _FakeRepo)
    monkeypatch.setattr(main, "create_session_factory", lambda: _FakeSessionFactory())
    fake_queue = _FakeQueue()
    monkeypatch.setattr(main.RedisTaskQueue, "from_settings", lambda: fake_queue)

    response = client.post(
        "/github/webhook",
        headers={"x-github-event": "issue_comment", "x-hub-signature-256": "sha256=x"},
        json={
            "issue": {"title": "Implement cache", "number": 42},
            "comment": {"body": "@agent repo=predictiv do it"},
            "repository": {"full_name": "acme/control"},
            "sender": {"login": "alex"},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["queued"] is True
    assert data["task_id"] == "task-123"
    assert fake_queue.enqueued == ["task-123"]


class _TimelineRepo:
    def __init__(self, session) -> None:  # noqa: ANN001
        self.session = session

    def list_recent(self, limit: int = 50) -> list[TaskRecord]:
        _ = limit
        now = datetime.now(UTC)
        return [
            TaskRecord(
                task_id="task-123",
                title="Implement cache",
                payload={
                    "repository": "acme/predictiv",
                    "source_repository": "acme/control",
                    "issue_number": 42,
                    "sender": "alex",
                    "body": "@agent repo=predictiv do it",
                },
                state=TaskState.AWAITING_APPROVAL,
                created_at=now,
                updated_at=now,
            )
        ]

    def get_by_id(self, task_id: str) -> TaskRecord | None:
        if task_id != "task-123":
            return None
        now = datetime.now(UTC)
        return TaskRecord(
            task_id="task-123",
            title="Implement cache",
            payload={"repository": "acme/predictiv"},
            state=TaskState.AWAITING_APPROVAL,
            created_at=now,
            updated_at=now,
        )

    def list_task_transitions(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        _ = limit
        if task_id is None and run_id is None:
            return []
        return [
            {
                "transition_id": "tr-1",
                "task_id": "task-123",
                "run_id": "run-1",
                "from_state": "received",
                "to_state": "normalized",
                "reason": "worker_normalize",
                "details": {"step": 1},
                "created_at": datetime.now(UTC),
            }
        ]

    def get_run(self, run_id: str) -> dict[str, object] | None:
        if run_id != "run-1":
            return None
        now = datetime.now(UTC)
        return {
            "run_id": "run-1",
            "task_id": "task-123",
            "status": "succeeded",
            "metadata": {"objective": "Implement cache"},
            "worker_name": "worker",
            "started_at": now,
            "ended_at": now,
            "created_at": now,
        }

    def get_latest_run_for_task(self, task_id: str) -> dict[str, object] | None:
        if task_id != "task-123":
            return None
        now = datetime.now(UTC)
        return {
            "run_id": "run-1",
            "task_id": "task-123",
            "status": "succeeded",
            "metadata": {"objective": "Implement cache"},
            "worker_name": "worker",
            "started_at": now,
            "ended_at": now,
            "created_at": now,
        }

    def list_run_events(self, run_id: str, limit: int = 500) -> list[dict[str, object]]:
        _ = limit
        if run_id != "run-1":
            return []
        return [
            {
                "event_id": "evt-1",
                "run_id": "run-1",
                "event_type": "plan_created",
                "payload": {"objective": "Implement cache"},
                "created_at": datetime.now(UTC),
            },
            {
                "event_id": "evt-2",
                "run_id": "run-1",
                "event_type": "pr_draft",
                "payload": {"title": "Draft title", "body": "Draft body"},
                "created_at": datetime.now(UTC),
            },
            {
                "event_id": "evt-3",
                "run_id": "run-1",
                "event_type": "approval_pr_created",
                "payload": {
                    "pull_request_number": 7,
                    "pull_request_url": "https://github.com/acme/predictiv/pull/7",
                    "branch_name": "agentic/run-1",
                    "base_branch": "main",
                },
                "created_at": datetime.now(UTC),
            }
        ]

    def get_poll_cursor(self, cursor_key: str) -> dict[str, object] | None:
        if cursor_key != "github_poll:issue_comments:italnstallion789/agentic_coder":
            return None
        return {
            "since": "2026-03-08T00:10:00Z",
            "last_comment_id": 321,
            "control_repository": "italnstallion789/agentic_coder",
            "last_polled_at": "2026-03-08T00:11:00+00:00",
            "last_seen_count": 5,
            "last_enqueued_count": 2,
        }


def test_task_timeline_endpoint(monkeypatch) -> None:
    from agentic_coder.api import main

    monkeypatch.setattr(main, "TaskRepository", _TimelineRepo)
    monkeypatch.setattr(main, "create_session_factory", lambda: _FakeSessionFactory())

    response = client.get("/tasks/task-123/timeline")

    assert response.status_code == 200
    data = response.json()
    assert data["task"]["task_id"] == "task-123"
    assert data["task"]["state"] == "awaiting_approval"
    assert len(data["timeline"]) == 1
    assert data["timeline"][0]["transition_id"] == "tr-1"


def test_run_replay_endpoint(monkeypatch) -> None:
    from agentic_coder.api import main

    monkeypatch.setattr(main, "TaskRepository", _TimelineRepo)
    monkeypatch.setattr(main, "create_session_factory", lambda: _FakeSessionFactory())

    response = client.get("/runs/run-1")

    assert response.status_code == 200
    data = response.json()
    assert data["run"]["run_id"] == "run-1"
    assert data["run"]["status"] == "succeeded"
    assert data["run"]["metadata"]["objective"] == "Implement cache"
    assert len(data["timeline"]) == 1
    assert len(data["events"]) >= 2
    assert data["events"][0]["event_type"] == "plan_created"


class _FakeGithubService:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.created: list[tuple[str, str]] = []

    async def create_installation_token(self, installation_id: int) -> str:
        _ = installation_id
        return "token"

    async def get_default_branch(self, repository: str, installation_token: str) -> str:
        _ = repository, installation_token
        return "main"

    async def get_branch_head_sha(
        self,
        repository: str,
        branch: str,
        installation_token: str,
    ) -> str:
        _ = repository, branch, installation_token
        return "abc123"

    async def create_branch(
        self,
        repository: str,
        branch: str,
        base_sha: str,
        installation_token: str,
    ) -> None:
        _ = repository, branch, base_sha, installation_token

    async def upsert_file(
        self,
        repository: str,
        installation_token: str,
        *,
        branch: str,
        path: str,
        message: str,
        content: str,
    ) -> dict[str, object]:
        _ = repository, installation_token, branch, message, content
        return {"commit": {"sha": "commit123"}, "content": {"path": path}}

    async def create_pull_request(
        self,
        repository: str,
        installation_token: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> dict[str, object]:
        _ = repository, installation_token, title, body, head, base, draft
        return {
            "id": 11,
            "number": 7,
            "html_url": "https://github.com/acme/widgets/pull/7",
            "state": "open",
            "draft": True,
        }


def test_create_pull_request_from_run(monkeypatch) -> None:
    from agentic_coder.api import main

    monkeypatch.setattr(main, "TaskRepository", _TimelineRepo)
    monkeypatch.setattr(main, "create_session_factory", lambda: _FakeSessionFactory())
    monkeypatch.setattr(main, "GitHubAppService", _FakeGithubService)

    response = client.post(
        "/runs/run-1/pull-request",
        json={"installation_id": 123, "branch_name": "agentic/run-1", "draft": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "run-1"
    assert data["repository"] == "acme/predictiv"
    assert data["artifact"]["path"] == ".agentic/runs/run-1.md"
    assert data["artifact"]["commit_sha"] == "commit123"
    assert data["pull_request"]["number"] == 7


def test_get_startup_self_check_endpoint() -> None:
    from agentic_coder.api import main

    main.app.state.startup_self_check = main.SelfCheckResponse(
        ok=True,
        checked_at="2026-03-08T00:00:00+00:00",
        checks={"app": {"ok": True}},
    )

    response = client.get("/startup/self-check")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["checks"]["app"]["ok"] is True


def test_rerun_startup_self_check_endpoint(monkeypatch) -> None:
    from agentic_coder.api import main

    async def _fake_self_check() -> main.SelfCheckResponse:
        return main.SelfCheckResponse(
            ok=True,
            checked_at="2026-03-08T00:00:00+00:00",
            checks={"repositories": [{"repository": "acme/predictiv", "ok": True}]},
        )

    monkeypatch.setattr(main, "run_github_self_check", _fake_self_check)

    response = client.post("/startup/self-check/run")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["checks"]["repositories"][0]["repository"] == "acme/predictiv"


def test_polling_status_endpoint(monkeypatch) -> None:
    from agentic_coder.api import main

    monkeypatch.setattr(main, "TaskRepository", _TimelineRepo)
    monkeypatch.setattr(main, "create_session_factory", lambda: _FakeSessionFactory())

    response = client.get("/polling/status")

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "polling"
    assert data["control_repository"] == "italnstallion789/agentic_coder"
    assert data["cursor"]["last_seen_count"] == 5
    assert data["cursor"]["last_enqueued_count"] == 2


def test_dashboard_data_endpoint(monkeypatch) -> None:
    from agentic_coder.api import main

    monkeypatch.setattr(main, "TaskRepository", _TimelineRepo)
    monkeypatch.setattr(main, "create_session_factory", lambda: _FakeSessionFactory())

    response = client.get("/dashboard/data")
    assert response.status_code == 200
    data = response.json()
    assert data["task_count"] == 1
    task = data["tasks"][0]
    assert task["request"]["target_repository"] == "acme/predictiv"
    assert task["run"]["run_id"] == "run-1"
    assert task["pull_request"]["number"] == 7
