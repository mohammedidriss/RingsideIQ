"""dataset_participant.code — participant codes (ADR-014)

Revision ID: 9c1e5a7d3f42
Revises: 7d3e9a1c5b20
Create Date: 2026-10-13

Each dataset participant gets a code (F-0001, F-0002, … per dataset) so the
dataset's files and exports name people by code, never by name. Existing
participants are numbered in the order they joined their dataset.

Idempotent guards, matching the other migrations here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c1e5a7d3f42"
down_revision: str | Sequence[str] | None = "7d3e9a1c5b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_dataset_participant_code"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "code" not in {c["name"] for c in insp.get_columns("dataset_participant")}:
        with op.batch_alter_table("dataset_participant") as batch:
            batch.add_column(sa.Column("code", sa.String(length=12), nullable=True))

    rows = bind.execute(
        sa.text(
            "SELECT id, dataset_id, code FROM dataset_participant ORDER BY dataset_id, created_at"
        )
    ).fetchall()
    used: dict[object, int] = {}
    for _, dataset_id, code in rows:
        if code and code.startswith("F-") and code[2:].isdigit():
            used[dataset_id] = max(used.get(dataset_id, 0), int(code[2:]))
    for row_id, dataset_id, code in rows:
        if code:
            continue
        used[dataset_id] = used.get(dataset_id, 0) + 1
        bind.execute(
            sa.text("UPDATE dataset_participant SET code = :code WHERE id = :id"),
            {"code": f"F-{used[dataset_id]:04d}", "id": row_id},
        )

    insp = sa.inspect(bind)
    if INDEX not in {ix["name"] for ix in insp.get_indexes("dataset_participant")}:
        op.create_index(INDEX, "dataset_participant", ["dataset_id", "code"], unique=True)


def downgrade() -> None:
    op.drop_index(INDEX, table_name="dataset_participant")
    with op.batch_alter_table("dataset_participant") as batch:
        batch.drop_column("code")
