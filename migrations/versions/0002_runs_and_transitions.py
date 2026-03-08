"""add runs and task transitions

Revision ID: 0002_runs_and_transitions
Revises: 0001_initial
Create Date: 2026-03-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0002_runs_and_transitions"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("worker_name", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", name="uq_runs_run_id"),
    )
    op.create_index("ix_runs_task_id", "runs", ["task_id"], unique=False)
    op.create_index("ix_runs_status", "runs", ["status"], unique=False)
    op.create_index("ix_runs_created_at", "runs", ["created_at"], unique=False)

    op.create_table(
        "task_transitions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("transition_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("from_state", sa.String(length=64), nullable=True),
        sa.Column("to_state", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=True),
        sa.Column("details", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("transition_id", name="uq_task_transitions_transition_id"),
    )
    op.create_index("ix_task_transitions_task_id", "task_transitions", ["task_id"], unique=False)
    op.create_index("ix_task_transitions_run_id", "task_transitions", ["run_id"], unique=False)
    op.create_index(
        "ix_task_transitions_created_at",
        "task_transitions",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_task_transitions_created_at", table_name="task_transitions")
    op.drop_index("ix_task_transitions_run_id", table_name="task_transitions")
    op.drop_index("ix_task_transitions_task_id", table_name="task_transitions")
    op.drop_table("task_transitions")

    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_task_id", table_name="runs")
    op.drop_table("runs")
