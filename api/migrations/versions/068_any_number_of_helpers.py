"""Any number of helpers on an operation.

Revision ID: 068_any_number_of_helpers
Revises: 067_pause_reasons_relabel
Create Date: 2026-11-16

operation_helpers.position was limited to 1 or 2 (two helpers). The check now
only asks for a position of 1 or more. No rows change: every existing row
already satisfies the new check.
"""

from alembic import op

revision = "068_any_number_of_helpers"
down_revision = "067_pause_reasons_relabel"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_operation_helper_position", "operation_helpers", type_="check")
    op.create_check_constraint(
        "ck_operation_helper_position", "operation_helpers", "position >= 1"
    )


def downgrade():
    # Only possible while no operation has more than two helpers.
    op.drop_constraint("ck_operation_helper_position", "operation_helpers", type_="check")
    op.create_check_constraint(
        "ck_operation_helper_position", "operation_helpers", "position IN (1, 2)"
    )
