"""Every automatic move of a scheduled job's first operation to a later start,
with the kind of delay that caused it."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class DelayKind:
    # Late or unordered materials set the new start.
    MATERIAL = "MATERIAL"
    # Only the passed start date moved the job.
    RESCHEDULED = "RESCHEDULED"
    # Earlier work of the same worker or unit is running past its planned end.
    OVERRUN = "OVERRUN"
    # The unit broke down; the work moved to another unit or after the repair.
    BREAKDOWN = "BREAKDOWN"
    # The assigned worker is absent, on leave or left for an emergency.
    WORKER_ABSENT = "WORKER_ABSENT"
    # An earlier step finished ahead of schedule, so the next steps start sooner.
    EARLY_FINISH = "EARLY_FINISH"


class MaterialCause:
    # The limiting delivery came after the date the supplier promised.
    SUPPLIER_LATE = "SUPPLIER_LATE"
    # Materials not ordered, or ordered too late to arrive by the planned start.
    NOT_ORDERED = "NOT_ORDERED"


class ScheduleMove(db.Model):
    __tablename__ = "schedule_moves"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    job_order_id = db.Column(
        db.String(36),
        db.ForeignKey("job_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = db.Column(db.String(20), nullable=False, index=True)
    previous_start = db.Column(db.DateTime(timezone=True), nullable=False)
    new_start = db.Column(db.DateTime(timezone=True), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    supplier_order_id = db.Column(
        db.String(36), db.ForeignKey("supplier_orders.id", ondelete="SET NULL"), nullable=True
    )
    # Supplier whose delivery set the new start (also for lines without a PO).
    supplier_id = db.Column(
        db.String(36), db.ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # MATERIAL moves only: MaterialCause.
    material_cause = db.Column(db.String(20), nullable=True)
    moved_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    # Who applied a manual move (Admin Reschedule, calendar, breakdown); null for automatic.
    moved_by_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    note = db.Column(db.Text, nullable=True)
    # Structured diff of worker / helpers / machine / start changes (JSONB).
    changes = db.Column(JSONB, nullable=True)

    job_order = db.relationship("JobOrder")
    supplier = db.relationship("Supplier")
    supplier_order = db.relationship("SupplierOrder")
    moved_by = db.relationship("User", foreign_keys=[moved_by_id])

    def to_dict(self) -> dict:
        actor = self.moved_by
        return {
            "id": self.id,
            "jobOrderId": self.job_order_id,
            "kind": self.kind,
            "previousStart": self.previous_start.isoformat() if self.previous_start else None,
            "newStart": self.new_start.isoformat() if self.new_start else None,
            "reason": self.reason,
            "note": self.note,
            "changes": self.changes,
            "materialCause": self.material_cause,
            "supplierOrderId": self.supplier_order_id,
            "supplierId": self.supplier_id,
            "movedAt": self.moved_at.isoformat() if self.moved_at else None,
            "movedById": self.moved_by_id,
            "movedByName": actor.full_name if actor else None,
        }
