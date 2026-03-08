from datetime import UTC, datetime

from agentic_coder.domain.tasks import TaskRecord, TaskState
from agentic_coder.orchestration.pipeline import TaskPipeline


def test_pipeline_runs_and_generates_outputs(tmp_path) -> None:
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
