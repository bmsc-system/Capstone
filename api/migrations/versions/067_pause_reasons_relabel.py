"""More pause reasons, and a record of an Admin re-labelling a pause.

Revision ID: 067_pause_reasons_relabel
Revises: 066_supplier_ordered_consumables
Create Date: 2026-11-16

Additive only:
  - operation_pause_reason: five new values, so fewer pauses end up as
    "Other": WAITING_TOOL, WAITING_INSTRUCTIONS, POWER_INTERRUPTION,
    MOVED_TO_OTHER_JOB, WORKER_AWAY.
  - operation_time_logs: three nullable columns. When the Admin changes the
    reason of a pause (for example an "Other" pause that was really a tool
    wait), the first reason, who changed it and when are kept.
"""

import sqlalchemy as sa
from alembic import op

revision = "067_pause_reasons_relabel"
down_revision = "066_supplier_ordered_consumables"
branch_labels = None
depends_on = None

NEW_REASONS = (
    "WAITING_TOOL",
    "WAITING_INSTRUCTIONS",
    "POWER_INTERRUPTION",
    "MOVED_TO_OTHER_JOB",
    "WORKER_AWAY",
)


def upgrade():
    op.add_column(
        "operation_time_logs",
        sa.Column("original_reason", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "operation_time_logs",
        sa.Column(
            "relabeled_by_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", name="fk_operation_time_logs_relabeled_by_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "operation_time_logs",
        sa.Column("relabeled_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A new enum value cannot be added inside a transaction that then uses it.
    with op.get_context().autocommit_block():
        for value in NEW_REASONS:
            op.execute(f"ALTER TYPE operation_pause_reason ADD VALUE IF NOT EXISTS '{value}'")


def downgrade():
    # The enum values stay (PostgreSQL cannot drop an enum value).
    op.drop_column("operation_time_logs", "relabeled_at")
    op.drop_constraint(
        "fk_operation_time_logs_relabeled_by_id", "operation_time_logs", type_="foreignkey"
    )
    op.drop_column("operation_time_logs", "relabeled_by_id")
    op.drop_column("operation_time_logs", "original_reason")
