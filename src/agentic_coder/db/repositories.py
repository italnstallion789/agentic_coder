from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from agentic_coder.db.models import PollCursorORM, RunEventORM, RunORM, TaskORM, TaskTransitionORM
from agentic_coder.domain.tasks import TaskRecord, TaskState
from agentic_coder.orchestration.state_machine import InvalidTaskTransitionError, TaskStateMachine


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._state_machine = TaskStateMachine()

    def create(self, title: str, payload: dict[str, object]) -> TaskRecord:
        task = TaskORM(
            task_id=str(uuid4()),
            title=title,
            payload=payload,
            state=TaskState.RECEIVED.value,
        )
        self.session.add(task)
        self.session.add(
            TaskTransitionORM(
                transition_id=str(uuid4()),
                task_id=task.task_id,
                run_id=None,
                from_state=None,
                to_state=TaskState.RECEIVED.value,
                reason="task_created",
                details={"source": "github_webhook"},
            )
        )
        self.session.commit()
        self.session.refresh(task)
        return self._to_record(task)

    def get_by_id(self, task_id: str) -> TaskRecord | None:
        stmt = select(TaskORM).where(TaskORM.task_id == task_id)
        task = self.session.scalar(stmt)
        if task is None:
            return None
        return self._to_record(task)

    def list_recent(self, limit: int = 50) -> list[TaskRecord]:
        stmt = select(TaskORM).order_by(TaskORM.created_at.desc()).limit(limit)
        tasks = self.session.scalars(stmt).all()
        return [self._to_record(task) for task in tasks]

    def update_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        run_id: str | None = None,
        reason: str | None = None,
        details: dict[str, object] | None = None,
    ) -> TaskRecord | None:
        stmt = select(TaskORM).where(TaskORM.task_id == task_id)
        task = self.session.scalar(stmt)
        if task is None:
            return None
        from_state = TaskState(task.state)
        if from_state != state:
            transition_probe = TaskRecord(
                task_id=task.task_id,
                title=task.title,
                payload=task.payload,
                state=from_state,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            try:
                self._state_machine.transition(transition_probe, state)
            except InvalidTaskTransitionError as exc:
                raise InvalidTaskTransitionError(
                    f"Invalid transition for task {task_id}: {from_state.value} -> {state.value}"
                ) from exc

        task.state = state.value
        task.updated_at = datetime.now(UTC)
        self.session.add(task)
        self.session.add(
            TaskTransitionORM(
                transition_id=str(uuid4()),
                task_id=task.task_id,
                run_id=run_id,
                from_state=from_state.value,
                to_state=state.value,
                reason=reason,
                details=details or {},
            )
        )
        self.session.commit()
        self.session.refresh(task)
        return self._to_record(task)

    def create_run(self, task_id: str, worker_name: str) -> str:
        run_id = str(uuid4())
        now = datetime.now(UTC)
        run = RunORM(
            run_id=run_id,
            task_id=task_id,
            status="running",
            worker_name=worker_name,
            started_at=now,
            ended_at=None,
        )
        self.session.add(run)
        self.session.commit()
        return run_id

    def complete_run(self, run_id: str, status: str) -> None:
        stmt = select(RunORM).where(RunORM.run_id == run_id)
        run = self.session.scalar(stmt)
        if run is None:
            return
        run.status = status
        run.ended_at = datetime.now(UTC)
        self.session.add(run)
        self.session.commit()

    def update_run_metadata(self, run_id: str, metadata: dict[str, object]) -> None:
        stmt = select(RunORM).where(RunORM.run_id == run_id)
        run = self.session.scalar(stmt)
        if run is None:
            return
        run.metadata_json = metadata
        self.session.add(run)
        self.session.commit()

    def append_run_event(self, run_id: str, event_type: str, payload: dict[str, object]) -> None:
        event = RunEventORM(
            event_id=str(uuid4()),
            run_id=run_id,
            event_type=event_type,
            payload=payload,
        )
        self.session.add(event)
        self.session.commit()

    def get_run(self, run_id: str) -> dict[str, object] | None:
        stmt = select(RunORM).where(RunORM.run_id == run_id)
        run = self.session.scalar(stmt)
        if run is None:
            return None
        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "status": run.status,
            "metadata": run.metadata_json,
            "worker_name": run.worker_name,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "created_at": run.created_at,
        }

    def get_latest_run_for_task(self, task_id: str) -> dict[str, object] | None:
        stmt = (
            select(RunORM)
            .where(RunORM.task_id == task_id)
            .order_by(RunORM.created_at.desc())
            .limit(1)
        )
        run = self.session.scalar(stmt)
        if run is None:
            return None
        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "status": run.status,
            "metadata": run.metadata_json,
            "worker_name": run.worker_name,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "created_at": run.created_at,
        }

    def list_run_events(self, run_id: str, limit: int = 500) -> list[dict[str, object]]:
        stmt = (
            select(RunEventORM)
            .where(RunEventORM.run_id == run_id)
            .order_by(RunEventORM.created_at.asc())
            .limit(limit)
        )
        events = self.session.scalars(stmt).all()
        return [
            {
                "event_id": event.event_id,
                "run_id": event.run_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at,
            }
            for event in events
        ]

    def list_task_transitions(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        stmt = select(TaskTransitionORM).order_by(TaskTransitionORM.created_at.asc())
        if task_id is not None:
            stmt = stmt.where(TaskTransitionORM.task_id == task_id)
        if run_id is not None:
            stmt = stmt.where(TaskTransitionORM.run_id == run_id)
        transitions = self.session.scalars(stmt.limit(limit)).all()

        return [
            {
                "transition_id": transition.transition_id,
                "task_id": transition.task_id,
                "run_id": transition.run_id,
                "from_state": transition.from_state,
                "to_state": transition.to_state,
                "reason": transition.reason,
                "details": transition.details,
                "created_at": transition.created_at,
            }
            for transition in transitions
        ]

    def get_poll_cursor(self, cursor_key: str) -> dict[str, object] | None:
        stmt = select(PollCursorORM).where(PollCursorORM.cursor_key == cursor_key)
        cursor = self.session.scalar(stmt)
        if cursor is None:
            return None
        return cursor.cursor_json

    def upsert_poll_cursor(self, cursor_key: str, cursor_json: dict[str, object]) -> None:
        stmt = select(PollCursorORM).where(PollCursorORM.cursor_key == cursor_key)
        cursor = self.session.scalar(stmt)
        if cursor is None:
            cursor = PollCursorORM(cursor_key=cursor_key, cursor_json=cursor_json)
        else:
            cursor.cursor_json = cursor_json
            cursor.updated_at = datetime.now(UTC)
        self.session.add(cursor)
        self.session.commit()

    def delete_task(self, task_id: str) -> bool:
        stmt = select(TaskORM).where(TaskORM.task_id == task_id)
        task = self.session.scalar(stmt)
        if task is None:
            return False
        self.session.delete(task)
        self.session.commit()
        return True

    def clear_all_requests(self, *, clear_poll_cursors: bool = True) -> dict[str, int]:
        run_events_deleted = self.session.execute(delete(RunEventORM)).rowcount or 0
        transitions_deleted = self.session.execute(delete(TaskTransitionORM)).rowcount or 0
        runs_deleted = self.session.execute(delete(RunORM)).rowcount or 0
        tasks_deleted = self.session.execute(delete(TaskORM)).rowcount or 0
        poll_cursors_deleted = 0
        if clear_poll_cursors:
            poll_cursors_deleted = self.session.execute(delete(PollCursorORM)).rowcount or 0
        self.session.commit()
        return {
            "tasks_deleted": int(tasks_deleted),
            "runs_deleted": int(runs_deleted),
            "transitions_deleted": int(transitions_deleted),
            "run_events_deleted": int(run_events_deleted),
            "poll_cursors_deleted": int(poll_cursors_deleted),
        }

    @staticmethod
    def _to_record(task: TaskORM) -> TaskRecord:
        return TaskRecord(
            task_id=task.task_id,
            title=task.title,
            payload=task.payload,
            state=TaskState(task.state),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
