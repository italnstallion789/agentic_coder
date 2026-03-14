from datetime import UTC, datetime
from pathlib import Path

from agentic_coder.domain.tasks import TaskRecord, TaskState
from agentic_coder.orchestration import pipeline as pipeline_module
from agentic_coder.orchestration.pipeline import TaskPipeline


class _Policy:
    class models:  # noqa: N801
        primary_provider = "github"
        fallback_provider = None


class _Settings:
    github_models_api_key = "ghp-test"
    github_models_chat_model = "Phi-4"
    github_models_base_url = "https://models.inference.ai.azure.com"
    ollama_chat_model = "llama3.1:8b"
    ollama_base_url = "http://ollama:11434"
    model_request_timeout_seconds = 40.0


def test_pipeline_runs_and_generates_outputs(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    task = TaskRecord(
        task_id="task-1",
        title="Add feature",
        payload={
            "title": "Add feature",
            "body": "Please add feature and tests",
            "repository": "acme/widgets",
        },
        state=TaskState.RECEIVED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    pipeline = TaskPipeline(workspace_root=tmp_path)
    result = pipeline.run(task)

    assert result.plan.objective == "Add feature"
    assert result.review.approved is True
    assert result.security.passed is True
    assert result.graph_summary["nodes"] >= 2
    assert result.indexed_files >= 1
    assert result.pr_draft.title.startswith("[acme/widgets]")


def test_pipeline_uses_model_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pipeline_module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        pipeline_module.PolicyLoader,
        "load",
        lambda self: _Policy(),
    )

    pipeline = TaskPipeline(
        workspace_root=tmp_path,
        model_provider_override="github",
        model_name_override="gpt-4.1",
    )

    assert pipeline._model_used == "github:gpt-4.1"
    assert pipeline.router.get("github").model == "gpt-4.1"
