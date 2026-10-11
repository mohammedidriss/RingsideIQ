"""imu_sample table

Revision ID: a1b2c3d4e5f6
Revises: c0dc926843da
Create Date: 2026-05-09 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "c0dc926843da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLModel.create_all may have already created the table (with its index)
    # at app startup; tolerate that so the migration is idempotent in dev DBs.
    # create_table below already emits the ix_imu_sample_session_id index via
    # index=True on session_id, so the explicit create_index must be guarded to
    # avoid a self-collision on a fresh DB (see c3d4e5f6a7b8 for the pattern).
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "imu_sample" not in insp.get_table_names():
        op.create_table(
            "imu_sample",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("session_id", sa.Uuid(), nullable=False, index=True),
            sa.Column("t_ms", sa.Float(), nullable=False),
            sa.Column("ax_g", sa.Float(), nullable=False),
            sa.Column("ay_g", sa.Float(), nullable=False),
            sa.Column("az_g", sa.Float(), nullable=False),
            sa.Column("gx_dps", sa.Float(), nullable=False, server_default="0"),
            sa.Column("gy_dps", sa.Float(), nullable=False, server_default="0"),
            sa.Column("gz_dps", sa.Float(), nullable=False, server_default="0"),
            sa.Column("hand", sa.String(length=10), nullable=True),
            sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        )

    insp = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in insp.get_indexes("imu_sample")}
    if "ix_imu_sample_session_id" not in existing_indexes:
        op.create_index(
            "ix_imu_sample_session_id",
            "imu_sample",
            ["session_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_imu_sample_session_id", table_name="imu_sample")
    op.drop_table("imu_sample")
