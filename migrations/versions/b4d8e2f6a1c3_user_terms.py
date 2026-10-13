"""user.terms_version, user.terms_accepted_at — terms acceptance (ADR-016)

Revision ID: b4d8e2f6a1c3
Revises: 9c1e5a7d3f42
Create Date: 2026-10-13

Which version of the terms and conditions each person accepted, and when. Both
start empty: existing accounts are asked to accept at their next sign-in.

Idempotent guards, matching the other migrations here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4d8e2f6a1c3"
down_revision: str | Sequence[str] | None = "9c1e5a7d3f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "user" not in insp.get_table_names():
        return  # a new database: the app creates `user` with these columns itself
    cols = {c["name"] for c in insp.get_columns("user")}
    with op.batch_alter_table("user") as batch:
        if "terms_version" not in cols:
            batch.add_column(sa.Column("terms_version", sa.String(length=40), nullable=True))
        if "terms_accepted_at" not in cols:
            batch.add_column(sa.Column("terms_accepted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if "user" not in sa.inspect(op.get_bind()).get_table_names():
        return
    with op.batch_alter_table("user") as batch:
        batch.drop_column("terms_accepted_at")
        batch.drop_column("terms_version")
