"""A period a production worker is away: marked absent by the Administrator,
on leave, or gone for an emergency in the middle of the day. The scheduler
treats the period as time the worker is not available."""

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class AbsenceReason:
    ABSENT = "ABSENT"
    EMERGENCY = "EMERGENCY"
    LEAVE = "LEAVE"

    ALL = (ABSENT, EMERGENCY, LEAVE)
    LABELS = {ABSENT: "Absent", EMERGENCY: "Emergency", LEAVE: "On leave"}


class WorkerAbsence(db.Model):
    __tablename__ = "worker_absences"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    worker_id = db.Column(
        db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    reason = db.Column(db.String(20), nullable=False, default=AbsenceReason.ABSENT)
    note = db.Column(db.String(255), nullable=True)
    created_by_id = db.Column(
        db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    # Set when the worker came back early; the period no longer blocks scheduling.
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True)

    worker = db.relationship("User", foreign_keys=[worker_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def to_dict(self):
        return {
            "id": self.id,
            "workerId": self.worker_id,
            "workerName": self.worker.full_name if self.worker else None,
            "startsAt": self.starts_at.isoformat() if self.starts_at else None,
            "endsAt": self.ends_at.isoformat() if self.ends_at else None,
            "reason": self.reason,
            "reasonLabel": AbsenceReason.LABELS.get(self.reason, self.reason),
            "note": self.note,
            "createdByName": self.created_by.full_name if self.created_by else None,
            "cancelledAt": self.cancelled_at.isoformat() if self.cancelled_at else None,
        }
