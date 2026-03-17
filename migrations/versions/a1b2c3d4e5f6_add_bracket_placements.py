# SPDX-License-Identifier: GPL-3.0-or-later
# © TOP Team
"""add_bracket_placements

Revision ID: a1b2c3d4e5f6
Revises: 3cd668248cad
Create Date: 2026-03-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "3cd668248cad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.scalar() > 0


def upgrade() -> None:
    for col in ("first_place", "second_place", "third_place_1", "third_place_2"):
        if not _column_exists("brackets", col):
            op.add_column("brackets", sa.Column(col, sa.Integer(), nullable=True))


def downgrade() -> None:
    for col in ("third_place_2", "third_place_1", "second_place", "first_place"):
        op.drop_column("brackets", col)
