"""Append-only operation time events and machine downtime records."""

import enum
import uuid
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class OperationTimeEvent(enum.Enum):
    START = "START"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    COMPLETE = "COMPLETE"


class OperationPauseReason(enum.Enum):
    END_OF_SHIFT = "END_OF_SHIFT"
    BREAK = "BREAK"
    MACHINE_DOWN = "MACHINE_DOWN"
    WAITING_MATERIAL = "WAITING_MATERIAL"
    WAITING_PRIOR_OPERATION = "WAITING_PRIOR_OPERATION"
    # The worker has to leave; their remaining work is handed over or moved.
    EMERGENCY = "EMERGENCY"
    OTHER = "OTHER"


PAUSE_REASON_LABELS = {
    OperationPauseReason.END_OF_SHIFT.value: "End of shift",
    OperationPauseReason.BREAK.value: "Break",
    OperationPauseReason.MACHINE_DOWN.value: "Machine breakdown",
    OperationPauseReason.WAITING_MATERIAL.value: "Waiting for material",
    OperationPauseReason.WAITING_PRIOR_OPERATION.value: "Waiting on prior operation",
    OperationPauseReason.EMERGENCY.value: "Emergency (had to leave)",
    OperationPauseReason.OTHER.value: "Other",
}


def pause_reason_label(reason) -> str:
    key = reason.value if isinstance(reason, OperationPauseReason) else str(reason or "")
    return PAUSE_REASON_LABELS.get(key) or key.replace("_", " ").capitalize()


class DowntimeCategory(enum.Enum):
    MECHANICAL_FAILURE = "MECHANICAL_FAILURE"
    ELECTRICAL_FAULT = "ELECTRICAL_FAULT"
    UNDER_REPAIR = "UNDER_REPAIR"
    WAITING_FOR_PARTS = "WAITING_FOR_PARTS"
    SCHEDULED_MAINTENANCE = "SCHEDULED_MAINTENANCE"
    OTHER = "OTHER"


DOWNTIME_CATEGORY_LABELS = {
    DowntimeCategory.MECHANICAL_FAILURE: "Mechanical failure",
    DowntimeCategory.ELECTRICAL_FAULT: "Electrical fault",
    DowntimeCategory.UNDER_REPAIR: "Under repair",
    DowntimeCategory.WAITING_FOR_PARTS: "Waiting for parts",
    DowntimeCategory.SCHEDULED_MAINTENANCE: "Scheduled maintenance",
    DowntimeCategory.OTHER: "Other",
}


class OperationTimeLog(db.Model):
    __tablename__ = "operation_time_logs"
    __table_args__ = (
        db.Index("ix_operation_time_logs_op_event_at", "operation_id", "event_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    operation_id = db.Column(
        db.String(36),
        db.ForeignKey("operations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    worker_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    event = db.Column(
        db.Enum(OperationTimeEvent, name="operation_time_event", native_enum=True),
        nullable=False,
    )
    event_at = db.Column(db.DateTime(timezone=True), nullable=False)
    reason = db.Column(
        db.Enum(OperationPauseReason, name="operation_pause_reason", native_enum=True),
        nullable=True,
    )
    note = db.Column(db.Text, nullable=True)
    # event_at is when it happened on the phone; received_at is when the server
    # got it (null for records not sent by a worker's device).
    received_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # DB column is nullable (historical); keep model aligned so flask db check is clean.
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)

    operation = db.relationship(
        "JobOperation", back_populates="time_logs"
    )
    worker = db.relationship("User", foreign_keys=[worker_id])

    def to_dict(self):
        return {
            "id": self.id,
            "operationId": self.operation_id,
            "workerId": self.worker_id,
            "workerName": self.worker.full_name if self.worker else None,
            "event": self.event.value if self.event else None,
            "eventAt": self.event_at.isoformat() if self.event_at else None,
            "receivedAt": self.received_at.isoformat() if self.received_at else None,
            "reason": self.reason.value if self.reason else None,
            "note": self.note,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }


class MachineDowntime(db.Model):
    __tablename__ = "machine_downtimes"
    __table_args__ = (
        db.Index("ix_machine_downtimes_unit_open", "machine_unit_id", "ended_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    machine_unit_id = db.Column(
        db.String(36),
        db.ForeignKey("machine_units.id"),
        nullable=False,
        index=True,
    )
    started_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ended_at = db.Column(db.DateTime(timezone=True), nullable=True)
    category = db.Column(db.Enum(DowntimeCategory), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    reported_by_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False
    )
    job_order_id = db.Column(
        db.String(36),
        db.ForeignKey("job_orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    operation_id = db.Column(
        db.String(36),
        db.ForeignKey("operations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    note = db.Column(db.Text, nullable=True)
    # Shop-local date; the unit stays unavailable for scheduling through this day.
    expected_repair_date = db.Column(db.Date, nullable=True)
    # started_at as reported (phone time); received_at is when the server got it.
    received_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)

    machine_unit = db.relationship("MachineUnit", back_populates="downtimes")
    reported_by = db.relationship("User", foreign_keys=[reported_by_id])
    operation = db.relationship("JobOperation", foreign_keys=[operation_id])

    def to_dict(self):
        return {
            "id": self.id,
            "machineUnitId": self.machine_unit_id,
            "machineUnitLabel": self.machine_unit.label if self.machine_unit else None,
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "endedAt": self.ended_at.isoformat() if self.ended_at else None,
            "receivedAt": self.received_at.isoformat() if self.received_at else None,
            "category": self.category.value if self.category else None,
            "reason": self.reason,
            "jobOrderId": self.job_order_id,
            "operationId": self.operation_id,
            "operationName": self.operation.operation_name if self.operation else None,
            "reportedById": self.reported_by_id,
            "reportedByName": self.reported_by.full_name if self.reported_by else None,
            "note": self.note,
            "expectedRepairDate": (
                self.expected_repair_date.isoformat() if self.expected_repair_date else None
            ),
            "open": self.ended_at is None,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }
