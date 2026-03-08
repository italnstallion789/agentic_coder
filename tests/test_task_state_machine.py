import pytest

from agentic_coder.domain.tasks import TaskRecord, TaskState
from agentic_coder.orchestration.state_machine import InvalidTaskTransitionError, TaskStateMachine


def test_valid_task_transition() -> None:
    task = TaskRecord(task_id="1", title="Test task", payload={})
    machine = TaskStateMachine()

    machine.transition(task, TaskState.NORMALIZED)

    assert task.state == TaskState.NORMALIZED


def test_invalid_task_transition() -> None:
    task = TaskRecord(task_id="1", title="Test task", payload={})
    machine = TaskStateMachine()

    with pytest.raises(InvalidTaskTransitionError):
        machine.transition(task, TaskState.RUNNING)
