from agentic_coder.worker import (
    is_target_repository_allowed,
    should_accept_body_as_command,
)


class _Policy:
    class system:  # noqa: N801
        allow_any_target_repository = False
        allowed_target_repositories = ["predictiv"]


def test_should_accept_body_as_command() -> None:
    assert should_accept_body_as_command("@agent implement this") is True
    assert should_accept_body_as_command("repo=predictiv do it") is True
    assert should_accept_body_as_command("regular comment") is False


def test_target_repository_allowlist_supports_repo_name_match() -> None:
    assert is_target_repository_allowed(_Policy(), "acme/predictiv") is True
    assert is_target_repository_allowed(_Policy(), "acme/control") is False
