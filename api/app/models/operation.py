import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class OperationStatus(enum.Enum):
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    REWORK = "REWORK"


class ReworkReasonCategory(enum.Enum):
    DIMENSION_OUT_OF_TOLERANCE = "DIMENSION_OUT_OF_TOLERANCE"
    SURFACE_FINISH = "SURFACE_FINISH"
    WRONG_MATERIAL = "WRONG_MATERIAL"
    MACHINE_FAULT = "MACHINE_FAULT"
    OPERATOR_ERROR = "OPERATOR_ERROR"
    OTHER = "OTHER"



class OperationHelper(db.Model):
    """A helper on an operation's crew. The lead is JobOperation.assigned_worker."""

    __tablename__ = "operation_helpers"
    __table_args__ = (
        db.CheckConstraint("position >= 1", name="ck_operation_helper_position"),
    )

    operation_id = db.Column(
        db.String(36), db.ForeignKey("operations.id", ondelete="CASCADE"), primary_key=True
    )
    worker_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), primary_key=True, index=True
    )
    position = db.Column(db.SmallInteger, nullable=False, default=1)

    operation = db.relationship("JobOperation", back_populates="helpers")
    worker = db.relationship("User", foreign_keys=[worker_id], lazy="joined")


class JobOperation(db.Model):
    """Shop-floor operation step within a job order (table: operations).

    The crew is the lead (assigned_worker) plus any number of helpers. Target hours
    are elapsed time for the whole crew, and every member is booked for the
    operation's working periods."""

    __tablename__ = "operations"
    __table_args__ = (
        db.UniqueConstraint("job_order_id", "sequence_no", name="uq_operation_job_seq"),
        db.Index("ix_operation_job_status", "job_order_id", "status"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    job_order_id = db.Column(
        db.String(36),
        db.ForeignKey("job_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_no = db.Column(db.Integer, nullable=False)
    operation_name = db.Column(db.String(255), nullable=False)
    operation_type_id = db.Column(
        db.String(36),
        db.ForeignKey("operation_types.id"),
        nullable=True,
        index=True,
    )
    machine_type_id = db.Column(
        db.String(36),
        db.ForeignKey("machine_types.id"),
        nullable=True,
        index=True,
    )
    machine_unit_id = db.Column(
        db.String(36),
        db.ForeignKey("machine_units.id"),
        nullable=True,
        index=True,
    )
    assigned_worker_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    estimated_hours = db.Column(db.Numeric(8, 2), nullable=True)
    scheduled_start = db.Column(db.DateTime(timezone=True), nullable=True)
    scheduled_end = db.Column(db.DateTime(timezone=True), nullable=True)
    actual_start = db.Column(db.DateTime(timezone=True), nullable=True)
    actual_end = db.Column(db.DateTime(timezone=True), nullable=True)
    actual_worked_hours = db.Column(db.Numeric(10, 4), nullable=True)
    variance_hours = db.Column(db.Numeric(10, 4), nullable=True)
    variance_pct = db.Column(db.Numeric(10, 4), nullable=True)
    status = db.Column(
        db.Enum(OperationStatus), nullable=False, default=OperationStatus.PENDING
    )
    rework_of_operation_id = db.Column(
        db.String(36),
        db.ForeignKey("operations.id"),
        nullable=True,
        index=True,
    )
    rework_reason = db.Column(db.Text, nullable=True)
    rework_reason_category = db.Column(
        db.Enum(ReworkReasonCategory), nullable=True
    )
    notes = db.Column(db.Text, nullable=True)
    # Outsourced operations (e.g. Heat Treatment): days at the outside shop.
    turnaround_days = db.Column(db.Integer, nullable=True)
    sent_out_date = db.Column(db.Date(), nullable=True)
    sent_to = db.Column(db.String(255), nullable=True)
    returned_date = db.Column(db.Date(), nullable=True)

    job_order = db.relationship("JobOrder", back_populates="operations")
    operation_type = db.relationship("OperationType", back_populates="operations")
    machine_type = db.relationship("MachineType", back_populates="operations")
    machine_unit = db.relationship("MachineUnit", back_populates="operations")
    assigned_worker = db.relationship(
        "User", back_populates="assigned_operations", foreign_keys=[assigned_worker_id]
    )
    helpers = db.relationship(
        "OperationHelper",
        back_populates="operation",
        cascade="all, delete-orphan",
        order_by="OperationHelper.position",
        lazy="selectin",
    )
    rework_of = db.relationship(
        "JobOperation",
        remote_side=[id],
        foreign_keys=[rework_of_operation_id],
        backref="rework_children",
    )
    time_logs = db.relationship(
        "OperationTimeLog",
        back_populates="operation",
        cascade="all, delete-orphan",
        order_by="OperationTimeLog.event_at",
    )

    @classmethod
    def crew_members_subquery(cls):
        """One row per (operation_id, worker_id) for every crew member, so
        per-worker figures credit the lead and each helper."""
        leads = db.select(
            cls.id.label("operation_id"), cls.assigned_worker_id.label("worker_id")
        ).where(cls.assigned_worker_id.isnot(None))
        helpers = db.select(
            OperationHelper.operation_id.label("operation_id"),
            OperationHelper.worker_id.label("worker_id"),
        )
        return db.union_all(leads, helpers).subquery("crew_members")

    @classmethod
    def crew_includes(cls, worker_id):
        """SQL filter: the worker is the lead or a helper."""
        return db.or_(
            cls.assigned_worker_id == worker_id,
            cls.helpers.any(OperationHelper.worker_id == worker_id),
        )

    @property
    def helper_ids(self) -> list[str]:
        return [h.worker_id for h in (self.helpers or [])]

    @property
    def crew_ids(self) -> list[str]:
        """Lead first, then helpers; empty when nobody is assigned."""
        if not self.assigned_worker_id:
            return []
        return [self.assigned_worker_id, *self.helper_ids]

    @property
    def crew_size(self) -> int:
        return len(self.crew_ids)

    def set_helpers(self, worker_ids) -> None:
        """Replace the helpers, keeping the given order."""
        wanted = [w for w in (worker_ids or []) if w]
        keep = {h.worker_id: h for h in (self.helpers or [])}
        self.helpers = []
        for pos, wid in enumerate(wanted, start=1):
            row = keep.get(wid) or OperationHelper(worker_id=wid)
            row.position = pos
            self.helpers.append(row)

    def crew_dicts(self, show_photo=None) -> list[dict]:
        """[{id, fullName, nickname, photoVersion, isLead}], lead first.
        ``show_photo(user_id)`` hides photos a viewer may not see."""
        people = []
        if self.assigned_worker:
            people.append((self.assigned_worker, True))
        people += [(h.worker, False) for h in (self.helpers or []) if h.worker]
        return [
            {
                "id": u.id,
                "fullName": u.full_name,
                "nickname": u.nickname,
                "photoVersion": (
                    u.photo_version if show_photo is None or show_photo(u.id) else None
                ),
                "isLead": lead,
            }
            for u, lead in people
        ]

    @property
    def is_outsourced(self) -> bool:
        return bool(self.operation_type and self.operation_type.is_outsourced)

    @property
    def expected_return_date(self):
        if not self.sent_out_date or self.turnaround_days is None:
            return None
        from datetime import timedelta

        return self.sent_out_date + timedelta(days=int(self.turnaround_days))

    def to_dict(
        self,
        schedule_by_worker=None,
        calendar_exceptions=None,
        open_downtime_unit_ids=None,
        include_material_wait=False,
        for_worker=False,
    ):
        def _num(v):
            if v is None:
                return None
            return float(v) if isinstance(v, Decimal) else float(v)

        job = self.job_order
        extra = {}
        if include_material_wait and job is not None:
            from app.services.material_purchase_service import material_wait_fields

            extra = material_wait_fields(job, for_worker=for_worker)
        return {
            **extra,
            "id": self.id,
            "jobOrderId": self.job_order_id,
            "jobTitle": job.title if job else None,
            "jobNumber": (
                f"JO-{(job.created_at.year if job and job.created_at else datetime.now(timezone.utc).year)}"
                f"-{(job.id or '')[:4].upper()}"
                if job
                else None
            ),
            "clientName": job.client.name if job and job.client else None,
            "dueDate": job.due_date.isoformat() if job and job.due_date else None,
            "jobPriority": job.priority.value if job and job.priority else None,
            "sequenceNo": self.sequence_no,
            "operationName": self.operation_name,
            "operationTypeId": self.operation_type_id,
            "operationTypeCode": self.operation_type.code if self.operation_type else None,
            "isOutsourced": self.is_outsourced,
            "turnaroundDays": self.turnaround_days,
            "sentOutDate": self.sent_out_date.isoformat() if self.sent_out_date else None,
            "sentTo": self.sent_to,
            "returnedDate": self.returned_date.isoformat() if self.returned_date else None,
            "expectedReturnDate": (
                self.expected_return_date.isoformat() if self.expected_return_date else None
            ),
            "machineTypeId": self.machine_type_id,
            "machineTypeCode": self.machine_type.code if self.machine_type else None,
            "machineTypeName": self.machine_type.name if self.machine_type else None,
            "machineUnitId": self.machine_unit_id,
            "machineUnitLabel": self.machine_unit.label if self.machine_unit else None,
            "assignedWorkerId": self.assigned_worker_id,
            "assignedWorkerName": (
                self.assigned_worker.full_name if self.assigned_worker else None
            ),
            "assignedWorkerNickname": (
                self.assigned_worker.nickname if self.assigned_worker else None
            ),
            "assignedWorkerPhotoVersion": (
                self.assigned_worker.photo_version if self.assigned_worker else None
            ),
            "helperIds": self.helper_ids,
            "crew": self.crew_dicts(),
            "crewSize": self.crew_size,
            # For information: hours worked by the crew together (elapsed x size).
            "laborHours": (
                _num(self.actual_worked_hours) * self.crew_size
                if self.actual_worked_hours is not None and self.crew_size
                else None
            ),
            "estimatedHours": _num(self.estimated_hours),
            "scheduledStart": self.scheduled_start.isoformat() if self.scheduled_start else None,
            "scheduledEnd": self.scheduled_end.isoformat() if self.scheduled_end else None,
            "segments": self._derived_segments(schedule_by_worker, calendar_exceptions),
            "actualStart": self.actual_start.isoformat() if self.actual_start else None,
            "actualEnd": self.actual_end.isoformat() if self.actual_end else None,
            "actualWorkedHours": _num(self.actual_worked_hours),
            "varianceHours": _num(self.variance_hours),
            "variancePct": _num(self.variance_pct),
            # Back-compat aliases used by older worker UI
            "startedAt": self.actual_start.isoformat() if self.actual_start else None,
            "completedAt": self.actual_end.isoformat() if self.actual_end else None,
            "status": self.status.value,
            "reworkOfOperationId": self.rework_of_operation_id,
            "reworkReason": self.rework_reason,
            "reworkReasonCategory": (
                self.rework_reason_category.value
                if self.rework_reason_category
                else None
            ),
            "notes": self.notes,
            "timeLogs": [log.to_dict() for log in (self.time_logs or [])],
            "isPaused": self._is_paused(),
            "machineDown": self._is_machine_down(open_downtime_unit_ids),
            # Legacy-shaped fields for gradual UI migration
            "seq": self.sequence_no,
            "name": self.operation_name,
            "machinesNeeded": (
                [self.machine_type.code] if self.machine_type else []
            ),
            "machineNames": (
                [self.machine_type.name] if self.machine_type else []
            ),
        }

    def _is_paused(self):
        logs = list(self.time_logs or [])
        if not logs:
            return False
        last = logs[-1]
        return last.event.value == "PAUSE" if last.event else False

    def _is_machine_down(self, open_downtime_unit_ids=None):
        if not self.machine_unit_id:
            return False
        if open_downtime_unit_ids is not None:
            return self.machine_unit_id in open_downtime_unit_ids
        from app.models.operation_time import MachineDowntime

        return (
            MachineDowntime.query.filter_by(
                machine_unit_id=self.machine_unit_id, ended_at=None
            ).first()
            is not None
        )

    def _derived_segments(self, schedule_by_worker=None, calendar_exceptions=None):
        if not self.scheduled_start or not self.scheduled_end or not self.assigned_worker_id:
            return []
        from app.services.schedule_calendar import (
            crew_schedule_map,
            derive_working_segments,
            load_calendar_exceptions,
            load_worker_schedule_maps_many,
            serialize_segments,
            utc_to_shop,
        )

        start = self.scheduled_start
        end = self.scheduled_end
        crew = self.crew_ids
        if schedule_by_worker is not None and all(w in schedule_by_worker for w in crew):
            maps = schedule_by_worker
        else:
            maps = load_worker_schedule_maps_many(crew)
        schedule_by_dow = crew_schedule_map([maps.get(w) or {} for w in crew])
        if calendar_exceptions is not None:
            exceptions = calendar_exceptions
        else:
            exceptions = load_calendar_exceptions(
                utc_to_shop(start).date(), utc_to_shop(end).date()
            )
        return serialize_segments(
            derive_working_segments(start, end, schedule_by_dow, exceptions)
        )


# Alias for import churn during refactor
Operation = JobOperation
