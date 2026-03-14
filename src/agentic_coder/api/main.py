import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from agentic_coder.config import get_settings
from agentic_coder.db.repositories import TaskRepository
from agentic_coder.db.session import create_session_factory
from agentic_coder.domain.tasks import TaskState
from agentic_coder.github_app.service import GitHubAppService, WebhookVerifier
from agentic_coder.logging import configure_logging
from agentic_coder.models.catalog import (
    available_chat_models,
    default_chat_model_selection,
    find_chat_model_option,
)
from agentic_coder.policy.loader import PolicyLoader, resolve_policy_path
from agentic_coder.pull_requests import apply_pull_request_changes, extract_proposed_file_changes
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

    if settings.github_startup_self_check_fail_fast:
        result = await run_github_self_check()
        app_instance.state.startup_self_check = result
        if not result.ok:
            raise RuntimeError("GitHub startup self-check failed")
        yield
        return

    app_instance.state.startup_self_check = SelfCheckResponse(
        ok=False,
        checked_at=datetime.now(UTC).isoformat(),
        checks={"pending": True, "reason": "startup self-check running in background"},
    )

    async def _background_self_check() -> None:
        try:
            app_instance.state.startup_self_check = await run_github_self_check()
        except Exception as exc:  # pragma: no cover - defensive background branch
            app_instance.state.startup_self_check = SelfCheckResponse(
                ok=False,
                checked_at=datetime.now(UTC).isoformat(),
                checks={"pending": False, "error": str(exc)},
            )

    task = asyncio.create_task(_background_self_check())
    try:
        yield
    finally:
        if not task.done():
            task.cancel()


app = FastAPI(title="Agentic Coder API", version="0.1.0", lifespan=lifespan)


class CreatePullRequestRequest(BaseModel):
    installation_id: int
    branch_name: str
    draft: bool = True


class CreateTaskRequest(BaseModel):
    title: str
    payload: dict[str, object] = Field(default_factory=dict)
    enqueue: bool = True


class CreateChatSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    target_repository: str = Field(min_length=1, max_length=256)
    approval_issue_number: int | None = Field(default=None, ge=1)
    model_provider: Literal["github", "ollama"] | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=256)
    metadata: dict[str, object] = Field(default_factory=dict)


class AppendChatMessageRequest(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    content: str = Field(min_length=1, max_length=8000)
    metadata: dict[str, object] = Field(default_factory=dict)


class ExecuteChatSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    target_repository: str | None = Field(default=None, max_length=256)
    include_transcript_limit: int = Field(default=40, ge=1, le=200)
    force_new: bool = False
    approval_issue_number: int | None = Field(default=None, ge=1)
    model_provider: Literal["github", "ollama"] | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=256)


class TaskDecisionRequest(BaseModel):
    reason: str | None = None


class ResetRequestsRequest(BaseModel):
    clear_poll_cursors: bool = True


class SelfCheckResponse(BaseModel):
    ok: bool
    checked_at: str
    checks: dict[str, Any]


def require_admin_token(
    x_admin_token: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    if settings.api_admin_token and x_admin_token != settings.api_admin_token:
        raise HTTPException(status_code=401, detail="Invalid admin token")


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


def normalize_operator_identity(value: str | None, *, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    collapsed = re.sub(r"\s+", "-", raw)
    normalized = re.sub(r"[^A-Za-z0-9_.:@/\-]+", "-", collapsed).strip("-")
    return (normalized or fallback)[:128]


def parse_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def parse_chat_model_selection(metadata: dict[str, object] | None) -> dict[str, str] | None:
    raw = (metadata or {}).get("model_selection")
    if not isinstance(raw, dict):
        return None

    provider = str(raw.get("provider") or "").strip()
    model = str(raw.get("model") or "").strip()
    if not provider or not model:
        return None
    return {
        "provider": provider,
        "model": model,
    }


def validate_chat_model_selection(
    *,
    settings: object,
    provider: str | None,
    model: str | None,
) -> dict[str, object] | None:
    normalized_provider = str(provider or "").strip()
    normalized_model = str(model or "").strip()
    if not normalized_provider and not normalized_model:
        return None
    if not normalized_provider or not normalized_model:
        raise HTTPException(
            status_code=400,
            detail="Both model_provider and model_name are required when selecting a model",
        )

    selected = find_chat_model_option(
        settings,
        provider=normalized_provider,
        model=normalized_model,
    )
    if selected is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported chat model selection: {normalized_provider}:{normalized_model}",
        )
    return selected


def serialize_chat_model_selection(
    *,
    settings: object,
    metadata: dict[str, object] | None,
    policy: object | None = None,
) -> dict[str, object] | None:
    selection = parse_chat_model_selection(metadata)
    if selection is not None:
        selected = find_chat_model_option(
            settings,
            provider=selection["provider"],
            model=selection["model"],
        )
        if selected is not None:
            return selected

    if policy is None:
        return None
    return default_chat_model_selection(policy, settings)


def serialize_chat_session_response(
    chat_session: dict[str, object],
    *,
    settings: object,
    policy: object,
) -> dict[str, object]:
    metadata = dict(chat_session.get("metadata") or {})
    return {
        **chat_session,
        "approval_issue_number": parse_positive_int(metadata.get("approval_issue_number")),
        "selected_model": serialize_chat_model_selection(
            settings=settings,
            metadata=metadata,
            policy=policy,
        ),
        "created_at": chat_session["created_at"].isoformat(),
        "updated_at": chat_session["updated_at"].isoformat(),
    }


def build_chat_transcript(
    messages: list[dict[str, object]],
    *,
    limit: int,
) -> str:
    relevant = messages[-max(1, limit) :]
    lines = ["Chat session transcript:"]
    for message in relevant:
        role = str(message.get("role") or "user").strip().lower()
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def resolve_repository_installation_id(repository: str) -> int:
    settings = get_settings()
    if not settings.github_app_id or not settings.github_private_key:
        raise HTTPException(status_code=500, detail="GitHub App credentials are missing")

    github = GitHubAppService(
        settings.github_app_id,
        settings.github_private_key,
        api_base_url=settings.github_api_base_url,
    )
    try:
        installation = await github.get_repository_installation(repository)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to resolve installation for {repository}: {exc}",
        ) from exc

    installation_id = installation.get("id")
    if not installation_id:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub installation id missing for repository {repository}",
        )
    return int(installation_id)


def _extract_pull_request(events: list[dict[str, object]]) -> dict[str, object] | None:
    pr_event = next(
        (event for event in reversed(events) if event["event_type"] == "approval_pr_created"),
        None,
    )
    if pr_event is None:
        return None
    payload = pr_event["payload"]
    return {
        "number": payload.get("pull_request_number"),
        "url": payload.get("pull_request_url"),
        "branch": payload.get("branch_name"),
        "base": payload.get("base_branch"),
        "commit_sha": payload.get("commit_sha"),
    }


def _build_dashboard_task_view(
    repo: TaskRepository,
    task: object,
) -> dict[str, object]:
    payload = task.payload
    latest_run = repo.get_latest_run_for_task(task.task_id)

    run_events: list[dict[str, object]] = []
    run_id: str | None = None
    run_status: str | None = None
    if latest_run is not None:
        run_id = str(latest_run["run_id"])
        run_status = str(latest_run["status"])
        run_events = repo.list_run_events(run_id=run_id, limit=500)

    proposal_event = next(
        (event for event in reversed(run_events) if event["event_type"] == "proposal_generated"),
        None,
    )
    pr_draft_event = next(
        (event for event in reversed(run_events) if event["event_type"] == "pr_draft"),
        None,
    )

    return {
        "task_id": task.task_id,
        "title": task.title,
        "state": task.state.value,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "request": {
            "source_repository": payload.get("source_repository"),
            "target_repository": payload.get("repository"),
            "issue_number": payload.get("issue_number"),
            "sender": payload.get("sender"),
            "body": payload.get("body"),
        },
        "run": {
            "run_id": run_id,
            "status": run_status,
            "worker_name": (latest_run or {}).get("worker_name") if latest_run else None,
            "proposal_summary": (
                (proposal_event or {}).get("payload") or {}
            ).get("summary"),
            "target_files": (
                (proposal_event or {}).get("payload") or {}
            ).get("target_files"),
            "pr_draft_title": ((pr_draft_event or {}).get("payload") or {}).get("title"),
            "model_used": ((latest_run or {}).get("metadata") or {}).get("model_used"),
        },
        "pull_request": _extract_pull_request(run_events),
    }


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


def resolve_base_branch_for_repository(policy: object, repository: str) -> str:
    per_repo = policy.system.target_base_branches
    if repository in per_repo:
        return str(per_repo[repository])
    repo_name = repository.split("/", maxsplit=1)[-1]
    if repo_name in per_repo:
        return str(per_repo[repo_name])
    return str(policy.system.default_target_base_branch)


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
    permissions = app_info.get("permissions") or {}
    required_permissions = {
        "metadata": "read",
        "contents": "write",
        "pull_requests": "write",
        "issues": "write",
    }
    permission_checks = {
        key: str(permissions.get(key) or "none") for key in required_permissions
    }
    permissions_ok = all(
        permission_checks[key] == required_permissions[key] for key in required_permissions
    )
    checks["app"] = {
        "ok": permissions_ok,
        "slug": app_info.get("slug"),
        "name": app_info.get("name"),
        "permissions": permission_checks,
        "required_permissions": required_permissions,
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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return health()


@app.get("/readyz")
def readiness() -> dict[str, object]:
    checks: dict[str, bool] = {"database": False, "redis": False}
    errors: dict[str, str] = {}

    session_factory = create_session_factory()
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:  # pragma: no cover - environment dependent branch
        errors["database"] = str(exc)

    try:
        queue = RedisTaskQueue.from_settings()
        queue.client.ping()
        checks["redis"] = True
    except Exception as exc:  # pragma: no cover - environment dependent branch
        errors["redis"] = str(exc)

    ready = all(checks.values())
    response = {"status": "ready" if ready else "not_ready", "checks": checks}
    if errors:
        response["errors"] = errors
    if not ready:
        raise HTTPException(status_code=503, detail=response)
    return response


@app.get("/startup/self-check")
def get_startup_self_check() -> dict[str, object]:
    result = getattr(app.state, "startup_self_check", None)
    if result is None:
        raise HTTPException(status_code=503, detail="Self-check not available yet")
    return result.model_dump()


@app.get("/self-check")
def get_self_check_alias() -> dict[str, object]:
    return get_startup_self_check()


@app.post("/startup/self-check/run")
async def rerun_startup_self_check() -> dict[str, object]:
    result = await run_github_self_check()
    app.state.startup_self_check = result
    return result.model_dump()


@app.get("/policy")
def get_policy() -> dict[str, object]:
    path, policy = load_policy()
    return {"path": str(path), "policy": policy.model_dump()}


@app.get("/polling/status")
def get_polling_status() -> dict[str, object]:
    _, policy = load_policy()
    control_repository = policy.system.control_repository
    cursor_key = (
        f"github_poll:issue_comments:{control_repository}" if control_repository else None
    )

    cursor: dict[str, object] | None = None
    if cursor_key:
        session_factory = create_session_factory()
        with session_factory() as session:
            repo = TaskRepository(session)
            cursor = repo.get_poll_cursor(cursor_key)

    return {
        "mode": policy.trigger.mode,
        "poll_interval_seconds": policy.trigger.poll_interval_seconds,
        "max_items_per_poll": policy.trigger.max_items_per_poll,
        "control_repository": control_repository,
        "cursor_key": cursor_key,
        "cursor": cursor,
    }


@app.get("/task-states")
def get_task_states() -> dict[str, list[str]]:
    return {"states": [state.value for state in TaskState]}


@app.post("/tasks")
def create_task(
    request_body: CreateTaskRequest,
    _admin: None = Depends(require_admin_token),
) -> dict[str, object]:
    payload = dict(request_body.payload)
    payload.setdefault("title", request_body.title)

    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        task = repo.create(title=request_body.title, payload=payload)

    queue_error: str | None = None
    if request_body.enqueue:
        try:
            queue = RedisTaskQueue.from_settings()
            queue.enqueue(task.task_id)
        except Exception as exc:  # pragma: no cover - non-critical runtime path
            queue_error = str(exc)

    return {
        "ok": True,
        "task_id": task.task_id,
        "state": task.state.value,
        "queued": request_body.enqueue and queue_error is None,
        "queue_error": queue_error,
    }


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


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        task = repo.get_by_id(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        latest_run = repo.get_latest_run_for_task(task.task_id)

    return {
        "task": {
            "task_id": task.task_id,
            "title": task.title,
            "payload": task.payload,
            "state": task.state.value,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        },
        "latest_run": (
            {
                **latest_run,
                "started_at": latest_run["started_at"].isoformat(),
                "ended_at": latest_run["ended_at"].isoformat() if latest_run["ended_at"] else None,
                "created_at": latest_run["created_at"].isoformat(),
            }
            if latest_run is not None
            else None
        ),
    }


@app.get("/dashboard/data")
def get_dashboard_data(limit: int = 50) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        tasks = repo.list_recent(limit=limit)

    _, policy = load_policy()
    polling = get_polling_status()
    settings = get_settings()

    runtime = "deterministic local pipeline"
    if policy.models.primary_provider == "auto":
        if settings.github_models_api_key:
            runtime = f"github:{settings.github_models_chat_model} (auto)"
        else:
            runtime = f"ollama:{settings.ollama_chat_model} (auto fallback)"
    elif policy.models.primary_provider == "github":
        runtime = f"github:{settings.github_models_chat_model}"
    elif policy.models.primary_provider == "ollama":
        runtime = f"ollama:{settings.ollama_chat_model}"

    with session_factory() as session:
        repo = TaskRepository(session)
        task_items = [_build_dashboard_task_view(repo, task) for task in tasks]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "autonomy_mode": policy.autonomy.mode,
        "task_count": len(task_items),
        "polling": polling,
        "model": {
            "primary_provider": policy.models.primary_provider,
            "fallback_provider": policy.models.fallback_provider,
            "embedding_provider": policy.models.embedding_provider,
            "runtime": runtime,
        },
        "tasks": task_items,
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> str:
    template_path = Path(__file__).with_name("dashboard.html")
    return template_path.read_text(encoding="utf-8")


@app.get("/chat", response_class=HTMLResponse)
def chat_page(_admin: None = Depends(require_admin_token)) -> str:
    template_path = Path(__file__).with_name("chat.html")
    settings = get_settings()
    _, policy = load_policy()
    chat_config = {
        "adminTokenRequired": bool(settings.api_admin_token),
        "availableModels": available_chat_models(settings),
        "defaultModelSelection": serialize_chat_model_selection(
            settings=settings,
            metadata=None,
            policy=policy,
        ),
        "costLegend": {
            "0x": "No premium Copilot token charge",
            "1x": "Premium Copilot token charge",
            "local": "Local runtime",
            "custom": "Configured model not in the curated catalog",
        },
    }
    return template_path.read_text(encoding="utf-8").replace(
        "__AGENTIC_CHAT_CONFIG__",
        json.dumps(chat_config),
    )


@app.post("/chat/sessions")
def create_chat_session(
    request_body: CreateChatSessionRequest,
    _admin: None = Depends(require_admin_token),
    x_operator: str | None = Header(default=None),
) -> dict[str, object]:
    settings = get_settings()
    _, policy = load_policy()
    resolved_repository = expand_target_repository(policy, request_body.target_repository)
    if not is_target_repository_allowed(policy, resolved_repository):
        raise HTTPException(
            status_code=403,
            detail=f"Target repository not allowed: {resolved_repository}",
        )

    created_by = normalize_operator_identity(x_operator, fallback="remote-operator")
    metadata = dict(request_body.metadata)
    if request_body.approval_issue_number is not None:
        metadata["approval_issue_number"] = request_body.approval_issue_number
    selected_model = validate_chat_model_selection(
        settings=settings,
        provider=request_body.model_provider,
        model=request_body.model_name,
    ) or default_chat_model_selection(policy, settings)
    if selected_model is not None:
        metadata["model_selection"] = {
            "provider": selected_model["provider"],
            "model": selected_model["model"],
        }

    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        created = repo.create_chat_session(
            title=request_body.title.strip() or "Untitled chat session",
            target_repository=resolved_repository,
            created_by=created_by,
            metadata=metadata,
        )

    return {
        "session": serialize_chat_session_response(
            created,
            settings=settings,
            policy=policy,
        )
    }


@app.get("/chat/sessions")
def list_chat_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    _admin: None = Depends(require_admin_token),
) -> dict[str, list[dict[str, object]]]:
    settings = get_settings()
    _, policy = load_policy()
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        sessions = repo.list_chat_sessions(limit=limit)
    return {
        "sessions": [
            serialize_chat_session_response(
                item,
                settings=settings,
                policy=policy,
            )
            for item in sessions
        ]
    }


@app.get("/chat/sessions/{session_id}")
def get_chat_session(
    session_id: str,
    _admin: None = Depends(require_admin_token),
) -> dict[str, object]:
    settings = get_settings()
    _, policy = load_policy()
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        chat_session = repo.get_chat_session(session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
    return {
        "session": serialize_chat_session_response(
            chat_session,
            settings=settings,
            policy=policy,
        )
    }


@app.get("/chat/sessions/{session_id}/messages")
def list_chat_messages(
    session_id: str,
    limit: int = Query(default=500, ge=1, le=1000),
    _admin: None = Depends(require_admin_token),
) -> dict[str, list[dict[str, object]]]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        chat_session = repo.get_chat_session(session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        messages = repo.list_chat_messages(session_id=session_id, limit=limit)
    return {
        "messages": [
            {
                **item,
                "created_at": item["created_at"].isoformat(),
            }
            for item in messages
        ]
    }


@app.post("/chat/sessions/{session_id}/messages")
def append_chat_message(
    session_id: str,
    request_body: AppendChatMessageRequest,
    _admin: None = Depends(require_admin_token),
) -> dict[str, object]:
    content = request_body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message content is required")

    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        chat_session = repo.get_chat_session(session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        message = repo.append_chat_message(
            session_id=session_id,
            role=request_body.role,
            content=content,
            metadata=request_body.metadata,
        )
    return {
        "message": {
            **message,
            "created_at": message["created_at"].isoformat(),
        }
    }


@app.get("/chat/sessions/{session_id}/runs")
def list_chat_session_runs(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    _admin: None = Depends(require_admin_token),
) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        chat_session = repo.get_chat_session(session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        tasks = repo.list_tasks_for_chat_session(session_id=session_id, limit=limit)
        task_views = [_build_dashboard_task_view(repo, task) for task in tasks]
    return {
        "session_id": session_id,
        "tasks": task_views,
    }


@app.post("/chat/sessions/{session_id}/execute")
async def execute_chat_session(
    session_id: str,
    request_body: ExecuteChatSessionRequest,
    _admin: None = Depends(require_admin_token),
    x_operator: str | None = Header(default=None),
) -> dict[str, object]:
    settings = get_settings()
    _, policy = load_policy()
    session_factory = create_session_factory()

    with session_factory() as session:
        repo = TaskRepository(session)
        chat_session = repo.get_chat_session(session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")

        target_repository = str(
            request_body.target_repository or chat_session["target_repository"]
        ).strip()
        if not target_repository:
            raise HTTPException(status_code=400, detail="Target repository is required")

        target_repository = expand_target_repository(policy, target_repository)
        if not is_target_repository_allowed(policy, target_repository):
            raise HTTPException(
                status_code=403,
                detail=f"Target repository not allowed: {target_repository}",
            )

        if not request_body.force_new:
            active_task = repo.get_active_chat_task(session_id=session_id)
            if active_task is not None:
                latest_run = repo.get_latest_run_for_task(active_task.task_id)
                return {
                    "ok": True,
                    "reused": True,
                    "task_id": active_task.task_id,
                    "state": active_task.state.value,
                    "run_id": (latest_run or {}).get("run_id"),
                }

        messages = repo.list_chat_messages(
            session_id=session_id,
            limit=request_body.include_transcript_limit,
        )
        if not messages:
            raise HTTPException(status_code=400, detail="Chat session has no messages")

    metadata = dict(chat_session.get("metadata") or {})
    explicit_selected_model = validate_chat_model_selection(
        settings=settings,
        provider=request_body.model_provider,
        model=request_body.model_name,
    )
    selected_model = (
        explicit_selected_model
        or serialize_chat_model_selection(
            settings=settings,
            metadata=metadata,
            policy=policy,
        )
    )
    if selected_model is not None:
        metadata["model_selection"] = {
            "provider": selected_model["provider"],
            "model": selected_model["model"],
        }
    approval_issue_number = (
        request_body.approval_issue_number
        if request_body.approval_issue_number is not None
        else parse_positive_int(metadata.get("approval_issue_number"))
    )

    source_repository = str(policy.system.control_repository or "").strip()
    if policy.autonomy.mode == "gated" and approval_issue_number is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Gated mode chat execution requires an approval_issue_number. "
                "Set it on the session or include it in execute payload."
            ),
        )

    target_installation_id = await resolve_repository_installation_id(target_repository)
    source_installation_id: int | None = None
    if approval_issue_number is not None:
        if not source_repository:
            raise HTTPException(
                status_code=400,
                detail="Control repository is required to route GitHub approvals",
            )
        source_installation_id = await resolve_repository_installation_id(source_repository)

    operator = normalize_operator_identity(x_operator, fallback="chat-ui")
    transcript = build_chat_transcript(
        messages,
        limit=request_body.include_transcript_limit,
    )
    task_title = (request_body.title or str(chat_session["title"]) or "Chat execution").strip()
    if not task_title:
        task_title = "Chat execution"

    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        if selected_model is not None:
            repo.update_chat_session_metadata(session_id=session_id, metadata=metadata)
        task = repo.create(
            title=task_title,
            payload={
                "event_name": "chat_session_execute",
                "source_repository": source_repository or "chat-ui",
                "repository": target_repository,
                "installation_id": target_installation_id,
                "target_installation_id": target_installation_id,
                "source_installation_id": source_installation_id,
                "title": task_title,
                "body": transcript,
                "sender": operator,
                "chat_session_id": session_id,
                "chat_message_count": len(messages),
                "requested_at": datetime.now(UTC).isoformat(),
                "issue_number": approval_issue_number,
                "approval_mode": "github_issue" if approval_issue_number else "none",
                "model_provider": (selected_model or {}).get("provider"),
                "model_name": (selected_model or {}).get("model"),
                "model_cost_tier": (selected_model or {}).get("costTier"),
            },
        )
        repo.append_chat_message(
            session_id=session_id,
            role="system",
            content=(
                f"Execution requested for {target_repository}; task {task.task_id}"
                + (
                    f"; approval issue #{approval_issue_number}"
                    if approval_issue_number is not None
                    else ""
                )
            ),
            metadata={
                "task_id": task.task_id,
                "target_repository": target_repository,
                "force_new": request_body.force_new,
                "approval_issue_number": approval_issue_number,
                "selected_model": selected_model,
            },
        )

    queue_error: str | None = None
    try:
        queue = RedisTaskQueue.from_settings()
        queue.enqueue(task.task_id)
    except Exception as exc:  # pragma: no cover - runtime env path
        queue_error = str(exc)

    return {
        "ok": True,
        "reused": False,
        "task_id": task.task_id,
        "state": task.state.value,
        "queued": queue_error is None,
        "queue_error": queue_error,
        "target_repository": target_repository,
        "installation_id": target_installation_id,
        "target_installation_id": target_installation_id,
        "source_installation_id": source_installation_id,
        "approval_issue_number": approval_issue_number,
        "selected_model": selected_model,
    }


@app.post("/tasks/{task_id}/approve")
def approve_task(
    task_id: str,
    _admin: None = Depends(require_admin_token),
) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        task = repo.get_by_id(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if task.state != TaskState.AWAITING_APPROVAL:
            raise HTTPException(status_code=400, detail="Task is not awaiting approval")
        updated = repo.update_state(task_id, TaskState.READY, reason="dashboard_approved")
        if updated is None:
            raise HTTPException(status_code=500, detail="Failed to update task state")
    return {"ok": True, "task_id": task_id, "state": "ready"}


@app.post("/tasks/{task_id}/reject")
def reject_task(
    task_id: str,
    request_body: TaskDecisionRequest,
    _admin: None = Depends(require_admin_token),
) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        task = repo.get_by_id(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if task.state != TaskState.AWAITING_APPROVAL:
            raise HTTPException(status_code=400, detail="Task is not awaiting approval")
        updated = repo.update_state(
            task_id,
            TaskState.CANCELLED,
            reason="dashboard_rejected",
            details={"reason": request_body.reason},
        )
        if updated is None:
            raise HTTPException(status_code=500, detail="Failed to update task state")
    return {"ok": True, "task_id": task_id, "state": "cancelled"}


@app.delete("/tasks/{task_id}")
def delete_task(
    task_id: str,
    _admin: None = Depends(require_admin_token),
) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        deleted = repo.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True, "task_id": task_id, "deleted": True}


@app.post("/admin/requests/reset")
def reset_requests(
    request_body: ResetRequestsRequest,
    _admin: None = Depends(require_admin_token),
) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        stats = repo.clear_all_requests(clear_poll_cursors=request_body.clear_poll_cursors)
    return {"ok": True, **stats}


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


@app.get("/runs/{run_id}/events")
def get_run_events(run_id: str, limit: int = 500) -> dict[str, object]:
    session_factory = create_session_factory()
    with session_factory() as session:
        repo = TaskRepository(session)
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        events = repo.list_run_events(run_id=run_id, limit=limit)

    return {
        "run_id": run_id,
        "events": [
            {
                **event,
                "created_at": event["created_at"].isoformat(),
            }
            for event in events
        ],
    }


@app.post("/github/webhook")
@app.post("/webhook")
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
    if not should_accept_body_as_command(normalized.body):
        return {
            "accepted": True,
            "event": x_github_event,
            "task_id": None,
            "queued": False,
            "queue_error": None,
            "normalized": None,
            "ignored": "non_command_comment",
        }

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
                "source_installation_id": normalized.installation_id,
                "target_installation_id": normalized.installation_id,
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
    _admin: None = Depends(require_admin_token),
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

    installation_id = int(
        task_payload.get("target_installation_id")
        or task_payload.get("installation_id")
        or request_body.installation_id
    )

    pr_title = str((run.get("metadata") or {}).get("pr_title") or task.title)
    pr_event = next((event for event in events if event["event_type"] == "pr_draft"), None)
    if pr_event is None:
        raise HTTPException(status_code=400, detail="PR draft metadata not found for run")
    pr_body = str(pr_event["payload"].get("body") or "Automated change set")
    file_changes = extract_proposed_file_changes(events)

    settings = get_settings()
    github = GitHubAppService(
        settings.github_app_id,
        settings.github_private_key,
        api_base_url=settings.github_api_base_url,
    )

    token = await github.create_installation_token(installation_id)
    base_branch = resolve_base_branch_for_repository(policy, repository)
    base_sha = await github.get_branch_head_sha(repository, base_branch, token)
    await github.create_branch(repository, request_body.branch_name, base_sha, token)

    applied = await apply_pull_request_changes(
        github=github,
        repository=repository,
        installation_token=token,
        branch=request_body.branch_name,
        run_id=run_id,
        task_id=task.task_id,
        requested_by="manual_api",
        pr_title=pr_title,
        pr_body=pr_body,
        file_changes=file_changes,
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
        "changed_files": applied.changed_files,
        "artifact": {
            "path": applied.artifact_path,
            "commit_sha": applied.artifact_commit_sha,
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
