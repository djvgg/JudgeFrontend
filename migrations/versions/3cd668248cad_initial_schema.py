"""initial_schema

Revision ID: 3cd668248cad
Revises:
Create Date: 2026-03-10 11:58:45.091469

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3cd668248cad"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    """Check if a column already exists (safe for shared DB with edv_backend)."""
    from alembic import op as _op

    conn = _op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchone()
    return result is not None


def _table_exists(table: str) -> bool:
    """Check if a table already exists."""
    from alembic import op as _op

    conn = _op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": table}
    ).fetchone()
    return result is not None


def upgrade() -> None:
    """Upgrade schema — safe to run against a DB already managed by edv_backend."""
    if not _table_exists("groups"):
        op.create_table(
            "groups",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("gender", sa.String(), nullable=True),
            sa.Column("age_group", sa.String(), nullable=True),
            sa.Column("weight_class", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _table_exists("brackets"):
        op.create_table(
            "brackets",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("group_id", sa.Integer(), nullable=True),
            sa.Column("mat_id", sa.Integer(), nullable=True),
            sa.Column("bracket_type", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _table_exists("participants"):
        op.create_table(
            "participants",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("first_name", sa.String(), nullable=True),
            sa.Column("last_name", sa.String(), nullable=True),
            sa.Column("gender", sa.String(), nullable=True),
            sa.Column("club", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _table_exists("group_participants"):
        op.create_table(
            "group_participants",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("group_id", sa.Integer(), nullable=True),
            sa.Column("participant_id", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _table_exists("fights"):
        op.create_table(
            "fights",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("bracket_id", sa.Integer(), nullable=True),
            sa.Column("participant1_id", sa.Integer(), nullable=True),
            sa.Column("participant2_id", sa.Integer(), nullable=True),
            sa.Column("fight_number", sa.Integer(), nullable=True),
            sa.Column("score1", sa.Integer(), nullable=True),
            sa.Column("score2", sa.Integer(), nullable=True),
            sa.Column("duration", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("bracket_phase", sa.String(), nullable=True),
            sa.Column("round", sa.Integer(), nullable=True),
            sa.Column("pos_in_round", sa.Integer(), nullable=True),
            sa.Column("pool_index", sa.Integer(), nullable=True),
            sa.Column("winner_id", sa.Integer(), nullable=True),
            sa.Column("table_id", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        # Table already exists (created by edv_backend) — add only missing columns
        if not _column_exists("fights", "fight_number"):
            op.add_column("fights", sa.Column("fight_number", sa.Integer(), nullable=True))
        if not _column_exists("fights", "score1"):
            op.add_column("fights", sa.Column("score1", sa.Integer(), nullable=True))
        if not _column_exists("fights", "score2"):
            op.add_column("fights", sa.Column("score2", sa.Integer(), nullable=True))
        if not _column_exists("fights", "duration"):
            op.add_column("fights", sa.Column("duration", sa.Integer(), nullable=True))
        if not _column_exists("fights", "table_id"):
            op.add_column("fights", sa.Column("table_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("fights")
    op.drop_table("group_participants")
    op.drop_table("participants")
    op.drop_table("brackets")
    op.drop_table("groups")
