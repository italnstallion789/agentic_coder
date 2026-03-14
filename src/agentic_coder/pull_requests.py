from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from agentic_coder.github_app.service import GitHubAppService


@dataclass(slots=True)
class ProposedFileChange:
    path: str
    content: str


@dataclass(slots=True)
class AppliedPullRequestChanges:
    changed_files: list[str]
    artifact_path: str
    artifact_commit_sha: str | None


def extract_proposed_file_changes(events: list[dict[str, object]]) -> list[ProposedFileChange]:
    proposal_event = next(
        (event for event in events if event.get("event_type") == "proposal_generated"),
        None,
    )
    payload = dict((proposal_event or {}).get("payload") or {})
    target_files = {
        str(path).strip()
        for path in (payload.get("target_files") or [])
        if str(path).strip()
    }
    raw_changes = payload.get("file_changes") or []
    changes: list[ProposedFileChange] = []
    seen_paths: set[str] = set()

    if not isinstance(raw_changes, list):
        return changes

    for item in raw_changes:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        content = str(item.get("content") or "")
        if not path or path in seen_paths:
            continue
        if not is_safe_repo_relative_path(path):
            continue
        if target_files and path not in target_files:
            continue
        seen_paths.add(path)
        changes.append(ProposedFileChange(path=path, content=content))

    return changes


def is_safe_repo_relative_path(path: str) -> bool:
    if not path or path.startswith("/") or path.startswith(".agentic/"):
        return False
    normalized = PurePosixPath(path)
    if normalized.is_absolute():
        return False
    parts = normalized.parts
    if not parts:
        return False
    return all(part not in ("", ".", "..") for part in parts)


def build_run_artifact_content(
    *,
    run_id: str,
    repository: str,
    task_id: str,
    requested_by: str,
    pr_title: str,
    pr_body: str,
    changed_files: list[str],
) -> str:
    changed_files_text = "\n".join(f"- {path}" for path in changed_files) or "- (artifact only)"
    return (
        f"# Run {run_id}\n\n"
        f"- Repository: {repository}\n"
        f"- Task ID: {task_id}\n"
        "- PR triggered by Agentic workflow\n"
        f"- Requested by: {requested_by}\n\n"
        "## Applied File Changes\n"
        f"{changed_files_text}\n\n"
        f"## PR Title\n{pr_title}\n\n"
        f"## PR Body\n{pr_body}\n"
    )


async def apply_pull_request_changes(
    *,
    github: GitHubAppService,
    repository: str,
    installation_token: str,
    branch: str,
    run_id: str,
    task_id: str,
    requested_by: str,
    pr_title: str,
    pr_body: str,
    file_changes: list[ProposedFileChange],
) -> AppliedPullRequestChanges:
    changed_files: list[str] = []

    for change in file_changes:
        commit_result = await github.upsert_file(
            repository,
            installation_token,
            branch=branch,
            path=change.path,
            message=f"agentic: update {change.path} for run {run_id}",
            content=change.content,
        )
        if not commit_result.get("skipped"):
            changed_files.append(change.path)

    artifact_path = f".agentic/runs/{run_id}.md"
    artifact_content = build_run_artifact_content(
        run_id=run_id,
        repository=repository,
        task_id=task_id,
        requested_by=requested_by,
        pr_title=pr_title,
        pr_body=pr_body,
        changed_files=changed_files,
    )
    artifact_result = await github.upsert_file(
        repository,
        installation_token,
        branch=branch,
        path=artifact_path,
        message=f"agentic: add run artifact {run_id}",
        content=artifact_content,
    )

    return AppliedPullRequestChanges(
        changed_files=changed_files,
        artifact_path=artifact_path,
        artifact_commit_sha=((artifact_result.get("commit") or {}).get("sha")),
    )
