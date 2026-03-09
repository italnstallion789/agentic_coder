"""add poll cursors

Revision ID: 0004_poll_cursors
Revises: 0003_run_events_and_metadata
Create Date: 2026-03-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0004_poll_cursors"
down_revision = "0003_run_events_and_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "poll_cursors",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("cursor_key", sa.String(length=128), nullable=False),
        sa.Column("cursor_json", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cursor_key", name="uq_poll_cursors_cursor_key"),
    )
    op.create_index("ix_poll_cursors_updated_at", "poll_cursors", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_poll_cursors_updated_at", table_name="poll_cursors")
    op.drop_table("poll_cursors")
