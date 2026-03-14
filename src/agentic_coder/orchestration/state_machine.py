from agentic_coder.domain.tasks import TaskRecord, TaskState

TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.RECEIVED: {TaskState.NORMALIZED, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.NORMALIZED: {
        TaskState.INDEXED,
        TaskState.PLANNED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.INDEXED: {TaskState.PLANNED, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.PLANNED: {TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.AWAITING_APPROVAL: {TaskState.READY, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.READY: {TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.RUNNING: {
        TaskState.AWAITING_APPROVAL,
        TaskState.READY,
        TaskState.DELEGATED,
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.DELEGATED: set(),
    TaskState.SUCCEEDED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}


class InvalidTaskTransitionError(ValueError):
    """Raised when a task transition is invalid."""


class TaskStateMachine:
    def transition(self, task: TaskRecord, target: TaskState) -> TaskRecord:
        allowed = TRANSITIONS[task.state]
        if target not in allowed:
            raise InvalidTaskTransitionError(f"Cannot transition {task.state} -> {target}")
        task.state = target
        return task
