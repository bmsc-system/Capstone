"""Consumable requests: items reported as running low or out from the shop floor.

Revision ID: 064_consumable_requests
Revises: 063_consumable_purchases
Create Date: 2026-11-09

Additive only: one new table and its indexes. No existing table or column is
changed. A partial unique index keeps at most one open report per item.
"""

import sqlalchemy as sa
from alembic import op

revision = "064_consumable_requests"
down_revision = "063_consumable_purchases"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "consumable_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tool_id", sa.String(36), sa.ForeignKey("tools.id"), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("reported_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "consumable_purchase_id",
            sa.String(36),
            sa.ForeignKey("consumable_purchases.id"),
            nullable=True,
        ),
        sa.Column(
            "material_purchase_id",
            sa.String(36),
            sa.ForeignKey("material_purchases.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_consumable_requests_tool_status", "consumable_requests", ["tool_id", "status"]
    )
    op.create_index(
        "uq_consumable_requests_one_open",
        "consumable_requests",
        ["tool_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )


def downgrade():
    op.drop_index("uq_consumable_requests_one_open", table_name="consumable_requests")
    op.drop_index("ix_consumable_requests_tool_status", table_name="consumable_requests")
    op.drop_table("consumable_requests")
