"""Consumables ordered from a supplier.

Revision ID: 066_supplier_ordered_consumables
Revises: 065_approval_handouts_absences
Create Date: 2026-11-15

Additive only:
  - tools: one new column, ordered_from_supplier (false by default). Most
    consumables are bought in cash at nearby stores; only those marked here
    are offered on a supplier purchase order.
  - Cutting oil, which the shop orders from a supplier by the gallon, is
    marked.
"""

import sqlalchemy as sa
from alembic import op

revision = "066_supplier_ordered_consumables"
down_revision = "065_approval_handouts_absences"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tools",
        sa.Column(
            "ordered_from_supplier",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        "UPDATE tools SET ordered_from_supplier = true "
        "WHERE lower(trim(name)) = 'cutting oil' AND category::text = 'CONSUMABLE'"
    )


def downgrade():
    op.drop_column("tools", "ordered_from_supplier")
