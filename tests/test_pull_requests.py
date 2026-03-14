from agentic_coder.pull_requests import extract_proposed_file_changes, is_safe_repo_relative_path


def test_is_safe_repo_relative_path_rejects_unsafe_values() -> None:
    assert is_safe_repo_relative_path("src/app.py") is True
    assert is_safe_repo_relative_path("../etc/passwd") is False
    assert is_safe_repo_relative_path("/tmp/file.py") is False
    assert is_safe_repo_relative_path(".agentic/runs/run-1.md") is False


def test_extract_proposed_file_changes_filters_invalid_paths() -> None:
    changes = extract_proposed_file_changes(
        [
            {
                "event_type": "proposal_generated",
                "payload": {
                    "target_files": ["src/app.py"],
                    "file_changes": [
                        {"path": "src/app.py", "content": "print('ok')\n"},
                        {"path": "../evil.py", "content": "print('nope')\n"},
                        {"path": "tests/test_app.py", "content": "print('extra')\n"},
                    ],
                },
            }
        ]
    )

    assert [change.path for change in changes] == ["src/app.py"]
