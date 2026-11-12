"""In-app alerts for staff (the header bell). One row per recipient, so each
person has their own read state."""

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class StaffAlertKind:
    MATERIAL_DELAY = "MATERIAL_DELAY"
    DELIVERY_OVERDUE = "DELIVERY_OVERDUE"
    JOB_AT_RISK = "JOB_AT_RISK"
    SINGLE_UNIT_DOWN = "SINGLE_UNIT_DOWN"
    NEEDS_REPLAN = "NEEDS_REPLAN"
    WORK_MOVED = "WORK_MOVED"
    OPERATION_STALLED = "OPERATION_STALLED"
    WORK_ON_DOWN_UNIT = "WORK_ON_DOWN_UNIT"
    REPAIR_DATE_PASSED = "REPAIR_DATE_PASSED"
    LOW_STOCK = "LOW_STOCK"
    OPERATION_DONE = "OPERATION_DONE"
    JOB_DONE = "JOB_DONE"


class StaffAlert(db.Model):
    __tablename__ = "staff_alerts"
    __table_args__ = (
        db.UniqueConstraint("recipient_id", "dedupe_key", name="uq_staff_alert_recipient_key"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    recipient_id = db.Column(
        db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind = db.Column(db.String(40), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=True)
    job_order_id = db.Column(
        db.String(36), db.ForeignKey("job_orders.id", ondelete="CASCADE"), nullable=True
    )
    supplier_order_id = db.Column(
        db.String(36), db.ForeignKey("supplier_orders.id", ondelete="SET NULL"), nullable=True
    )
    # Optional: the same alert is not raised twice for one recipient (repeat checks).
    dedupe_key = db.Column(db.String(160), nullable=True)
    read_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, index=True)

    job_order = db.relationship("JobOrder")
    supplier_order = db.relationship("SupplierOrder")

    def to_dict(self):
        job = self.job_order
        order = self.supplier_order
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "message": self.message,
            "jobOrderId": self.job_order_id,
            "jobNumber": job.job_number if job else None,
            "supplierOrderId": self.supplier_order_id,
            "poNumber": order.po_number if order else None,
            "read": self.read_at is not None,
            "readAt": self.read_at.isoformat() if self.read_at else None,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }
