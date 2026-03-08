import pytest

from agentic_coder.github_app.service import GitHubAppService


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    async def post(
        self,
        url: str,
        headers: dict[str, str],
        json: dict[str, object] | None = None,
    ):
        _ = headers
        self.requests.append(("POST", url, json))
        if url.endswith("/access_tokens"):
            return _FakeResponse(201, {"token": "inst-token"})
        if url.endswith("/git/refs"):
            return _FakeResponse(201, {"ref": "refs/heads/feature/test"})
        if url.endswith("/pulls"):
            return _FakeResponse(
                201,
                {
                    "id": 10,
                    "number": 2,
                    "html_url": "https://github.com/acme/widgets/pull/2",
                    "state": "open",
                    "draft": True,
                },
            )
        raise AssertionError(f"Unexpected POST url: {url}")

    async def get(self, url: str, headers: dict[str, str]):
        _ = headers
        self.requests.append(("GET", url, None))
        if url.endswith("/repos/acme/widgets"):
            return _FakeResponse(200, {"default_branch": "main"})
        if url.endswith("/repos/acme/widgets/git/ref/heads/main"):
            return _FakeResponse(200, {"object": {"sha": "abc123"}})
        raise AssertionError(f"Unexpected GET url: {url}")


@pytest.mark.asyncio
async def test_installation_token_and_pr_workflow(monkeypatch) -> None:
    monkeypatch.setattr("agentic_coder.github_app.service.httpx.AsyncClient", _FakeAsyncClient)

    service = GitHubAppService(app_id="1", private_key="test-key", api_base_url="https://api.github.com")
    monkeypatch.setattr(service, "create_app_jwt", lambda: "jwt-token")

    token = await service.create_installation_token(99)
    assert token == "inst-token"

    default_branch = await service.get_default_branch("acme/widgets", token)
    assert default_branch == "main"

    sha = await service.get_branch_head_sha("acme/widgets", default_branch, token)
    assert sha == "abc123"

    await service.create_branch("acme/widgets", "feature/test", sha, token)

    pr = await service.create_pull_request(
        "acme/widgets",
        token,
        title="Test PR",
        body="Body",
        head="feature/test",
        base="main",
        draft=True,
    )

    assert pr["number"] == 2
    assert pr["draft"] is True


def test_normalize_issue_comment_extracts_target_repository() -> None:
    service = GitHubAppService(app_id="1", private_key="k")
    normalized = service.normalize_issue_comment_event(
        "issue_comment",
        {
            "issue": {"title": "Do work", "number": 1},
            "comment": {"body": "@agent repo=acme/widgets implement cache"},
            "repository": {"full_name": "acme/control"},
            "installation": {"id": 99},
            "sender": {"login": "alex"},
        },
    )

    assert normalized.source_repository == "acme/control"
    assert normalized.target_repository == "acme/widgets"
    assert normalized.installation_id == 99


def test_normalize_issue_comment_resolves_bare_repo_name_to_source_owner() -> None:
    service = GitHubAppService(app_id="1", private_key="k")
    normalized = service.normalize_issue_comment_event(
        "issue_comment",
        {
            "issue": {"title": "Do work", "number": 1},
            "comment": {"body": "@agent repo=predictiv implement cache"},
            "repository": {"full_name": "acme/control"},
            "installation": {"id": 99},
            "sender": {"login": "alex"},
        },
    )

    assert normalized.source_repository == "acme/control"
    assert normalized.target_repository == "acme/predictiv"
