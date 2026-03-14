from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class TaskState(StrEnum):
    RECEIVED = "received"
    NORMALIZED = "normalized"
    INDEXED = "indexed"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    RUNNING = "running"
    DELEGATED = "delegated"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    title: str
    payload: dict[str, Any]
    state: TaskState = TaskState.RECEIVED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
