"""add run events and run metadata

Revision ID: 0003_run_events_and_metadata
Revises: 0002_runs_and_transitions
Create Date: 2026-03-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0003_run_events_and_metadata"
down_revision = "0002_runs_and_transitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))

    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("event_id", name="uq_run_events_event_id"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"], unique=False)
    op.create_index("ix_run_events_created_at", "run_events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_run_events_created_at", table_name="run_events")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")

    op.drop_column("runs", "metadata_json")
