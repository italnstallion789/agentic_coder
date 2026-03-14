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


def test_chat_dispatch_transition_path() -> None:
    task = TaskRecord(task_id="1", title="Dispatch task", payload={})
    machine = TaskStateMachine()

    machine.transition(task, TaskState.NORMALIZED)
    machine.transition(task, TaskState.PLANNED)
    machine.transition(task, TaskState.RUNNING)
    machine.transition(task, TaskState.DELEGATED)

    assert task.state == TaskState.DELEGATED
