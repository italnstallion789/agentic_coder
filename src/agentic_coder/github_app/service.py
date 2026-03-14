import hashlib
import hmac
import re
import time
from base64 import b64decode, b64encode
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt

from agentic_coder.github_app.models import NormalizedGithubTask


class WebhookVerifier:
    def __init__(self, secret: str) -> None:
        self.secret = secret.encode("utf-8")

    def verify(self, body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        expected = "sha256=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


class GitHubAppService:
    def __init__(
        self,
        app_id: str,
        private_key: str,
        api_base_url: str = "https://api.github.com",
    ) -> None:
        self.app_id = app_id
        self.private_key = private_key
        self.api_base_url = api_base_url.rstrip("/")

    def create_app_jwt(self) -> str:
        if not self.app_id or not self.private_key:
            raise ValueError("GitHub App credentials are not configured")
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 600, "iss": self.app_id},
            self.private_key,
            algorithm="RS256",
        )

    def normalize_issue_comment_event(
        self,
        event_name: str,
        payload: dict[str, Any],
    ) -> NormalizedGithubTask:
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        source_repository = (payload.get("repository") or {}).get("full_name", "unknown/unknown")
        body = comment.get("body", "")
        target_hint = self.extract_target_repository(body)
        target_repository = self.resolve_target_repository(source_repository, target_hint)
        sender = (payload.get("sender") or {}).get("login", "unknown")
        installation_id = (payload.get("installation") or {}).get("id")
        return NormalizedGithubTask(
            event_name=event_name,
            source_repository=source_repository,
            target_repository=target_repository,
            installation_id=installation_id,
            title=issue.get("title", "Untitled issue"),
            body=body,
            issue_number=issue.get("number"),
            sender=sender,
            raw_event=payload,
        )

    def normalize_polled_issue_comment(
        self,
        source_repository: str,
        installation_id: int,
        comment: dict[str, Any],
    ) -> NormalizedGithubTask:
        body = str(comment.get("body") or "")
        target_hint = self.extract_target_repository(body)
        target_repository = self.resolve_target_repository(source_repository, target_hint)
        sender = ((comment.get("user") or {}).get("login")) or "unknown"

        issue_url = str(comment.get("issue_url") or "")
        issue_number: int | None = None
        if issue_url.rsplit("/", maxsplit=1)[-1].isdigit():
            issue_number = int(issue_url.rsplit("/", maxsplit=1)[-1])

        title = f"Issue comment from {sender}"
        if issue_number is not None:
            title = f"Issue #{issue_number} comment from {sender}"

        return NormalizedGithubTask(
            event_name="issue_comment",
            source_repository=source_repository,
            target_repository=target_repository,
            installation_id=installation_id,
            title=title,
            body=body,
            issue_number=issue_number,
            sender=sender,
            raw_event={"polling": True, "comment": comment},
        )

    async def list_issue_comments_since(
        self,
        repository: str,
        installation_token: str,
        *,
        since: str | None,
        per_page: int = 50,
    ) -> list[dict[str, Any]]:
        url = f"{self.api_base_url}/repos/{repository}/issues/comments"
        headers = self._installation_headers(installation_token)
        params: dict[str, Any] = {
            "sort": "updated",
            "direction": "asc",
            "per_page": per_page,
        }
        if since:
            params["since"] = since

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()

        comments: list[dict[str, Any]] = response.json()
        comments.sort(
            key=lambda item: (
                str(item.get("updated_at") or datetime.now(UTC).isoformat()),
                int(item.get("id") or 0),
            )
        )
        return comments

    @staticmethod
    def extract_target_repository(comment_body: str) -> str | None:
        patterns = [
            r"(?:^|\s)repo\s*[:=]\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
            r"(?:^|\s)repo\s*[:=]\s*([A-Za-z0-9_.-]+)",
            r"(?:^|\s)/repo\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
            r"(?:^|\s)/repo\s+([A-Za-z0-9_.-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, comment_body)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def resolve_target_repository(source_repository: str, target_hint: str | None) -> str:
        if not target_hint:
            return source_repository
        if "/" in target_hint:
            return target_hint

        source_owner = source_repository.split("/", maxsplit=1)[0]
        if source_owner:
            return f"{source_owner}/{target_hint}"
        return target_hint

    async def create_installation_token(self, installation_id: int) -> str:
        app_jwt = self.create_app_jwt()
        url = f"{self.api_base_url}/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers)

        response.raise_for_status()
        token = response.json().get("token")
        if not token:
            raise ValueError("GitHub installation token missing in response")
        return token

    async def get_app_info(self) -> dict[str, Any]:
        app_jwt = self.create_app_jwt()
        url = f"{self.api_base_url}/app"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    async def get_repository_installation(self, repository: str) -> dict[str, Any]:
        app_jwt = self.create_app_jwt()
        url = f"{self.api_base_url}/repos/{repository}/installation"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        response.raise_for_status()
        return response.json()

    async def get_default_branch(self, repository: str, installation_token: str) -> str:
        url = f"{self.api_base_url}/repos/{repository}"
        headers = self._installation_headers(installation_token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        branch = response.json().get("default_branch")
        if not branch:
            raise ValueError("GitHub repository default branch not found")
        return branch

    async def get_branch_head_sha(
        self,
        repository: str,
        branch: str,
        installation_token: str,
    ) -> str:
        url = f"{self.api_base_url}/repos/{repository}/git/ref/heads/{branch}"
        headers = self._installation_headers(installation_token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        sha = (response.json().get("object") or {}).get("sha")
        if not sha:
            raise ValueError("GitHub branch head SHA not found")
        return sha

    async def create_branch(
        self,
        repository: str,
        branch: str,
        base_sha: str,
        installation_token: str,
    ) -> None:
        url = f"{self.api_base_url}/repos/{repository}/git/refs"
        headers = self._installation_headers(installation_token)
        payload = {"ref": f"refs/heads/{branch}", "sha": base_sha}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code not in (201, 422):
            response.raise_for_status()

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
    ) -> dict[str, Any]:
        url = f"{self.api_base_url}/repos/{repository}/pulls"
        headers = self._installation_headers(installation_token)
        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "draft": draft,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)

        response.raise_for_status()
        return response.json()

    async def create_issue_comment(
        self,
        repository: str,
        issue_number: int,
        installation_token: str,
        body: str,
    ) -> dict[str, Any]:
        url = f"{self.api_base_url}/repos/{repository}/issues/{issue_number}/comments"
        headers = self._installation_headers(installation_token)
        payload = {"body": body}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)

        response.raise_for_status()
        return response.json()

    async def add_issue_labels(
        self,
        repository: str,
        issue_number: int,
        installation_token: str,
        labels: list[str],
    ) -> dict[str, Any]:
        url = f"{self.api_base_url}/repos/{repository}/issues/{issue_number}/labels"
        headers = self._installation_headers(installation_token)
        payload = {"labels": labels}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)

        response.raise_for_status()
        return {"labels": response.json()}

    async def get_file_sha(
        self,
        repository: str,
        path: str,
        ref: str,
        installation_token: str,
    ) -> str | None:
        file_info = await self.get_file(repository, path, ref, installation_token)
        if file_info is None:
            return None
        return str(file_info.get("sha") or "") or None

    async def get_file(
        self,
        repository: str,
        path: str,
        ref: str,
        installation_token: str,
    ) -> dict[str, Any] | None:
        url = f"{self.api_base_url}/repos/{repository}/contents/{path}"
        headers = self._installation_headers(installation_token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers, params={"ref": ref})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        encoded_content = str(payload.get("content") or "")
        if payload.get("encoding") == "base64" and encoded_content:
            payload["decoded_content"] = b64decode(encoded_content).decode("utf-8")
        return payload

    async def upsert_file(
        self,
        repository: str,
        installation_token: str,
        *,
        branch: str,
        path: str,
        message: str,
        content: str,
    ) -> dict[str, Any]:
        existing_file = await self.get_file(repository, path, branch, installation_token)
        existing_sha = (existing_file or {}).get("sha")
        existing_content = (existing_file or {}).get("decoded_content")
        if existing_content == content:
            return {
                "commit": {},
                "content": {"path": path},
                "skipped": True,
            }
        encoded_content = b64encode(content.encode("utf-8")).decode("utf-8")
        url = f"{self.api_base_url}/repos/{repository}/contents/{path}"
        headers = self._installation_headers(installation_token)
        payload: dict[str, Any] = {
            "message": message,
            "content": encoded_content,
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _installation_headers(installation_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {installation_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
