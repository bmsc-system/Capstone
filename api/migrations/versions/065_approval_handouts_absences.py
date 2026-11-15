"""Supplier order approval, who handed out a consumable, worker away periods,
and the Emergency pause reason.

Revision ID: 065_approval_handouts_absences
Revises: 064_consumable_requests
Create Date: 2026-11-15

Additive only. Nothing existing is changed or removed:
  - supplier_orders: five new nullable columns for the Admin's approval
    (who sent the draft and when, the PO number and date asked for, and the
    note when it is sent back).
  - tool_events: one new nullable column, recorded_by_id (the Office Staff who
    handed a consumable to a worker).
  - worker_absences: a new table (a worker marked absent, on leave, or gone
    for an emergency; the scheduler does not book them in that period).
  - operation_pause_reason: one new value, EMERGENCY.
"""

import sqlalchemy as sa
from alembic import op

revision = "065_approval_handouts_absences"
down_revision = "064_consumable_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("supplier_orders", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "supplier_orders",
        sa.Column(
            "submitted_by_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("supplier_orders", sa.Column("requested_po_seq", sa.Integer(), nullable=True))
    op.add_column("supplier_orders", sa.Column("requested_issue_date", sa.Date(), nullable=True))
    op.add_column("supplier_orders", sa.Column("return_note", sa.Text(), nullable=True))

    op.add_column(
        "tool_events",
        sa.Column(
            "recorded_by_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_table(
        "worker_absences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "worker_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column(
            "created_by_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_worker_absences_worker_id", "worker_absences", ["worker_id"])
    op.create_index("ix_worker_absences_ends_at", "worker_absences", ["ends_at"])

    # A new enum value cannot be added inside a transaction that then uses it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE operation_pause_reason ADD VALUE IF NOT EXISTS 'EMERGENCY'")


def downgrade():
    # The EMERGENCY enum value stays (PostgreSQL cannot drop an enum value).
    op.drop_index("ix_worker_absences_ends_at", table_name="worker_absences")
    op.drop_index("ix_worker_absences_worker_id", table_name="worker_absences")
    op.drop_table("worker_absences")
    op.drop_column("tool_events", "recorded_by_id")
    op.drop_column("supplier_orders", "return_note")
    op.drop_column("supplier_orders", "requested_issue_date")
    op.drop_column("supplier_orders", "requested_po_seq")
    op.drop_column("supplier_orders", "submitted_by_id")
    op.drop_column("supplier_orders", "submitted_at")
