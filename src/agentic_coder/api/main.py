from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from agentic_coder.config import get_settings
from agentic_coder.db.repositories import TaskRepository
from agentic_coder.db.session import create_session_factory
from agentic_coder.domain.tasks import TaskState
from agentic_coder.github_app.service import GitHubAppService, WebhookVerifier
from agentic_coder.logging import configure_logging
from agentic_coder.policy.loader import PolicyLoader, resolve_policy_path
from agentic_coder.queue.redis_queue import RedisTaskQueue

configure_logging()


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    settings = get_settings()
    if not settings.github_startup_self_check:
        app_instance.state.startup_self_check = SelfCheckResponse(
            ok=True,
            checked_at=datetime.now(UTC).isoformat(),
            checks={"skipped": True, "reason": "github_startup_self_check=false"},
        )
        yield
        return

    result = await run_github_self_check()
    app_instance.state.startup_self_check = result
    if settings.github_startup_self_check_fail_fast and not result.ok:
        raise RuntimeError("GitHub startup self-check failed")
    yield


app = FastAPI(title="Agentic Coder API", version="0.1.0", lifespan=lifespan)


class CreatePullRequestRequest(BaseModel):
    installation_id: int
    branch_name: str
    draft: bool = True


class SelfCheckResponse(BaseModel):
    ok: bool
    checked_at: str
    checks: dict[str, Any]


def load_policy() -> tuple[Path, object]:
    settings = get_settings()
    policy_path = resolve_policy_path(Path.cwd() / settings.policy_path.parent)
    policy = PolicyLoader(policy_path).load()
    return policy_path, policy


def is_target_repository_allowed(policy: object, repository: str) -> bool:
    if policy.system.allow_any_target_repository:
        return True
    allowed = policy.system.allowed_target_repositories
    if repository in allowed:
        return True

    repo_name = repository.split("/", maxsplit=1)[-1]
    return repo_name in allowed


def expand_target_repository(policy: object, repository: str) -> str:
    if "/" in repository:
        return repository

    control_repo = policy.system.control_repository
    if control_repo and "/" in control_repo:
        owner = control_repo.split("/", maxsplit=1)[0]
        return f"{owner}/{repository}"
    return repository


async def run_github_self_check() -> SelfCheckResponse:
    settings = get_settings()
    _, policy = load_policy()

    checks: dict[str, Any] = {
        "credentials": {
            "app_id_set": bool(settings.github_app_id),
            "private_key_set": bool(settings.github_private_key),
            "webhook_secret_set": bool(settings.github_webhook_secret),
        },
        "app": {"ok": False},
        "repositories": [],
    }

    if not settings.github_app_id or not settings.github_private_key:
        return SelfCheckResponse(
            ok=False,
            checked_at=datetime.now(UTC).isoformat(),
            checks=checks,
        )

    github = GitHubAppService(
        settings.github_app_id,
        settings.github_private_key,
        api_base_url=settings.github_api_base_url,
    )

    app_info = await github.get_app_info()
    checks["app"] = {
        "ok": True,
        "slug": app_info.get("slug"),
        "name": app_info.get("name"),
    }

    repositories_to_check: list[str] = []
    control_repo = policy.system.control_repository
    if control_repo:
        repositories_to_check.append(control_repo)

    for repository in policy.system.allowed_target_repositories:
        resolved = expand_target_repository(policy, repository)
        if resolved not in repositories_to_check:
            repositories_to_check.append(resolved)

    repos_ok = True
    for repository in repositories_to_check:
        entry: dict[str, Any] = {"repository": repository, "ok": False}
        try:
            installation = await github.get_repository_installation(repository)
            installation_id = installation.get("id")
            if not installation_id:
                raise ValueError("installation id missing")

            token = await github.create_installation_token(int(installation_id))
            default_branch = await github.get_default_branch(repository, token)
            entry.update(
                {
                    "ok": True,
                    "installation_id": installation_id,
                    "default_branch": default_branch,
                }
            )
        except Exception as exc:  # pragma: no cover - external API branch
            repos_ok = False
            entry["error"] = str(exc)
        checks["repositories"].append(entry)

    return SelfCheckResponse(
        ok=checks["app"]["ok"] and repos_ok,
        checked_at=datetime.now(UTC).isoformat(),
        checks=checks,
    )


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    _, policy = load_policy()
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "autonomy_mode": policy.autonomy.mode,
    }


@app.get("/startup/self-check")
def get_startup_self_check() -> dict[str, object]:
    result = getattr(app.state, "startup_self_check", None)
    if result is None:
        raise HTTPException(status_code=503, detail="Self-check not available yet")
    return result.model_dump()


@app.post("/startup/self-check/run")
async def rerun_startup_self_check() -> dict[str, object]:
    result = await run_github_self_check()
    app.state.startup_self_check = result
    return result.model_dump()


@app.get("/policy")
def get_policy() -> dict[str, object]:
    path, policy = load_policy()
    return {"path": str(path), "policy": policy.model_dump()}


@app.get("/task-states")
def get_task_states() -> dict[str, list[str]]:
    return {"states": [state.value for state in TaskState]}


@app.get("/tasks")
def list_tasks(limit: int = 20) -> dict[str, list[dict[str, object]]]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        tasks = repo.list_recent(limit=limit)

    return {
        "tasks": [
            {
                "task_id": task.task_id,
                "title": task.title,
                "state": task.state.value,
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
            }
            for task in tasks
        ]
    }


@app.get("/tasks/{task_id}/timeline")
def get_task_timeline(task_id: str, limit: int = 200) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        task = repo.get_by_id(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        transitions = repo.list_task_transitions(task_id=task_id, limit=limit)

    return {
        "task": {
            "task_id": task.task_id,
            "title": task.title,
            "state": task.state.value,
        },
        "timeline": [
            {
                **transition,
                "created_at": transition["created_at"].isoformat(),
            }
            for transition in transitions
        ],
    }


@app.get("/runs/{run_id}")
def get_run(run_id: str, limit: int = 200) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        transitions = repo.list_task_transitions(run_id=run_id, limit=limit)
        events = repo.list_run_events(run_id=run_id, limit=limit)

    return {
        "run": {
            **run,
            "started_at": run["started_at"].isoformat(),
            "ended_at": run["ended_at"].isoformat() if run["ended_at"] else None,
            "created_at": run["created_at"].isoformat(),
        },
        "timeline": [
            {
                **transition,
                "created_at": transition["created_at"].isoformat(),
            }
            for transition in transitions
        ],
        "events": [
            {
                **event,
                "created_at": event["created_at"].isoformat(),
            }
            for event in events
        ],
    }


@app.post("/github/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default="unknown"),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, object]:
    settings = get_settings()
    verifier = WebhookVerifier(settings.github_webhook_secret)
    body = await request.body()
    if not verifier.verify(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    if x_github_event != "issue_comment":
        return {"accepted": True, "event": x_github_event, "normalized": None}

    github = GitHubAppService(settings.github_app_id, settings.github_private_key)
    normalized = github.normalize_issue_comment_event(x_github_event, payload)
    _, policy = load_policy()
    if not is_target_repository_allowed(policy, normalized.target_repository):
        raise HTTPException(
            status_code=403,
            detail=f"Target repository not allowed: {normalized.target_repository}",
        )

    session_factory = create_session_factory()
    with session_factory() as session:
        task_repo = TaskRepository(session)
        task = task_repo.create(
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
            },
        )

    queue_error: str | None = None
    try:
        queue = RedisTaskQueue.from_settings()
        queue.enqueue(task.task_id)
    except Exception as exc:  # pragma: no cover - non-critical runtime path
        queue_error = str(exc)

    return {
        "accepted": True,
        "event": x_github_event,
        "task_id": task.task_id,
        "queued": queue_error is None,
        "queue_error": queue_error,
        "normalized": {
            "source_repository": normalized.source_repository,
            "target_repository": normalized.target_repository,
            "installation_id": normalized.installation_id,
            "title": normalized.title,
            "body": normalized.body,
            "issue_number": normalized.issue_number,
            "sender": normalized.sender,
        },
    }


@app.post("/runs/{run_id}/pull-request")
async def create_pull_request_from_run(
    run_id: str,
    request_body: CreatePullRequestRequest,
) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        task = repo.get_by_id(str(run["task_id"]))
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found for run")

        events = repo.list_run_events(run_id=run_id)

    task_payload = task.payload
    repository = str(task_payload.get("repository") or "")
    if not repository:
        raise HTTPException(status_code=400, detail="Run task does not include repository")

    _, policy = load_policy()
    if not is_target_repository_allowed(policy, repository):
        raise HTTPException(status_code=403, detail=f"Target repository not allowed: {repository}")

    installation_id = int(task_payload.get("installation_id") or request_body.installation_id)

    pr_title = str((run.get("metadata") or {}).get("pr_title") or task.title)
    pr_event = next((event for event in events if event["event_type"] == "pr_draft"), None)
    if pr_event is None:
        raise HTTPException(status_code=400, detail="PR draft metadata not found for run")
    pr_body = str(pr_event["payload"].get("body") or "Automated change set")

    settings = get_settings()
    github = GitHubAppService(
        settings.github_app_id,
        settings.github_private_key,
        api_base_url=settings.github_api_base_url,
    )

    token = await github.create_installation_token(installation_id)
    base_branch = await github.get_default_branch(repository, token)
    base_sha = await github.get_branch_head_sha(repository, base_branch, token)
    await github.create_branch(repository, request_body.branch_name, base_sha, token)

    artifact_path = f".agentic/runs/{run_id}.md"
    artifact_content = (
        f"# Run {run_id}\n\n"
        f"- Repository: {repository}\n"
        f"- Task ID: {task.task_id}\n"
        f"- Run status: {run['status']}\n\n"
        f"## PR Draft\n\n"
        f"### Title\n{pr_title}\n\n"
        f"### Body\n{pr_body}\n"
    )
    commit_result = await github.upsert_file(
        repository,
        token,
        branch=request_body.branch_name,
        path=artifact_path,
        message=f"agentic: add run artifact {run_id}",
        content=artifact_content,
    )

    pull_request = await github.create_pull_request(
        repository,
        token,
        title=pr_title,
        body=pr_body,
        head=request_body.branch_name,
        base=base_branch,
        draft=request_body.draft,
    )

    return {
        "repository": repository,
        "run_id": run_id,
        "artifact": {
            "path": artifact_path,
            "commit_sha": ((commit_result.get("commit") or {}).get("sha")),
        },
        "pull_request": {
            "id": pull_request.get("id"),
            "number": pull_request.get("number"),
            "url": pull_request.get("html_url"),
            "state": pull_request.get("state"),
            "draft": pull_request.get("draft"),
        },
    }


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
