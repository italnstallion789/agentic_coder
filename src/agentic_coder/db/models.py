from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentic_coder.db.base import Base


class TaskORM(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_state", "state"),
        Index("ix_tasks_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class RunORM(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_task_id", "task_id"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    worker_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class TaskTransitionORM(Base):
    __tablename__ = "task_transitions"
    __table_args__ = (
        Index("ix_task_transitions_task_id", "task_id"),
        Index("ix_task_transitions_run_id", "run_id"),
        Index("ix_task_transitions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transition_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("runs.run_id", ondelete="SET NULL"),
        nullable=True,
    )
    from_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_state: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class RunEventORM(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        Index("ix_run_events_run_id", "run_id"),
        Index("ix_run_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class PollCursorORM(Base):
    __tablename__ = "poll_cursors"
    __table_args__ = (Index("ix_poll_cursors_updated_at", "updated_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cursor_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    cursor_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ChatSessionORM(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_target_repository", "target_repository"),
        Index("ix_chat_sessions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    target_repository: Mapped[str] = mapped_column(String(256), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ChatMessageORM(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_session_id", "session_id"),
        Index("ix_chat_messages_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(String(16000), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
