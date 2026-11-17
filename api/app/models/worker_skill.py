import enum
import uuid

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


class WorkerSkill(db.Model):
    """A worker's 1-5 level on a machine type. Operations without a machine
    take no skill: every worker qualifies for them."""

    __tablename__ = "worker_skills"
    __table_args__ = (
        db.UniqueConstraint("worker_id", "machine_type_id", name="uq_worker_skill_machine"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    worker_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    machine_type_id = db.Column(
        db.String(36),
        db.ForeignKey("machine_types.id"),
        nullable=False,
        index=True,
    )
    proficiency = db.Column(db.Integer, nullable=False, default=3)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)

    worker = db.relationship("User", back_populates="skills")
    machine_type = db.relationship("MachineType", back_populates="worker_skills")

    def to_dict(self):
        return {
            "id": self.id,
            "workerId": self.worker_id,
            "machineTypeId": self.machine_type_id,
            "machineTypeCode": self.machine_type.code if self.machine_type else None,
            "machineTypeName": self.machine_type.name if self.machine_type else None,
            "proficiency": self.proficiency,
            "isPrimary": self.is_primary,
        }


class WorkerSchedule(db.Model):
    __tablename__ = "worker_schedules"
    __table_args__ = (
        db.UniqueConstraint("worker_id", "day_of_week", name="uq_worker_schedule_day"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    worker_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_of_week = db.Column(db.Integer, nullable=False)  # 0=Mon … 6=Sun
    start_time = db.Column(db.Time(), nullable=True)
    end_time = db.Column(db.Time(), nullable=True)
    is_working = db.Column(db.Boolean, nullable=False, default=True)

    worker = db.relationship("User", back_populates="schedules")

    def to_dict(self):
        return {
            "id": self.id,
            "workerId": self.worker_id,
            "dayOfWeek": self.day_of_week,
            "startTime": self.start_time.strftime("%H:%M") if self.start_time else None,
            "endTime": self.end_time.strftime("%H:%M") if self.end_time else None,
            "isWorking": self.is_working,
        }


class CalendarExceptionType(enum.Enum):
    OVERTIME = "OVERTIME"
    SPECIAL_WORKING_DAY = "SPECIAL_WORKING_DAY"
    HOLIDAY_NO_WORK = "HOLIDAY_NO_WORK"


class WorkCalendarException(db.Model):
    __tablename__ = "work_calendar_exceptions"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    date = db.Column(db.Date(), nullable=False, index=True)
    type = db.Column(db.Enum(CalendarExceptionType), nullable=False)
    start_time = db.Column(db.Time(), nullable=True)
    end_time = db.Column(db.Time(), nullable=True)
    note = db.Column(db.String(255), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.date.isoformat() if self.date else None,
            "type": self.type.value,
            "startTime": self.start_time.strftime("%H:%M") if self.start_time else None,
            "endTime": self.end_time.strftime("%H:%M") if self.end_time else None,
            "note": self.note,
        }


class OperationType(db.Model):
    __tablename__ = "operation_types"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    code = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    default_machine_type_id = db.Column(
        db.String(36),
        db.ForeignKey("machine_types.id"),
        nullable=True,
        index=True,
    )
    active = db.Column(db.Boolean, nullable=False, default=True)
    # Done by an outside shop: no worker or machine, a turnaround in days instead of hours.
    is_outsourced = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    default_turnaround_days = db.Column(db.Integer, nullable=True)

    default_machine_type = db.relationship(
        "MachineType", back_populates="operation_types"
    )
    operations = db.relationship("JobOperation", back_populates="operation_type")

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "defaultMachineTypeId": self.default_machine_type_id,
            "defaultMachineTypeCode": (
                self.default_machine_type.code if self.default_machine_type else None
            ),
            "defaultMachineTypeName": (
                self.default_machine_type.name if self.default_machine_type else None
            ),
            "active": self.active,
            "isOutsourced": bool(self.is_outsourced),
            "defaultTurnaroundDays": self.default_turnaround_days,
        }


# Shared catalog for seed + migration
OPERATION_TYPE_SEED = [
    {"code": "BLANKING", "name": "Blanking", "machine": "LATHE"},
    {"code": "TURNING", "name": "Turning", "machine": "LATHE"},
    {"code": "FACING", "name": "Facing", "machine": "LATHE"},
    {"code": "THREADING", "name": "Threading", "machine": "LATHE"},
    {"code": "TEETH_CUTTING", "name": "Teeth Cutting", "machine": "MILLING"},
    {"code": "SLOTTING", "name": "Slotting", "machine": "MILLING"},
    {"code": "GROOVING", "name": "Grooving", "machine": "MILLING"},
    {"code": "DRILLING", "name": "Drilling", "machine": "DRILLING"},
    {"code": "KEYWAY", "name": "Keyway", "machine": "SHAPER"},
    {"code": "SPLINE", "name": "Spline", "machine": "SHAPER"},
    {"code": "SURFACE_GRINDING", "name": "Surface Grinding", "machine": "GRINDING"},
    {"code": "HEAT_TREATMENT", "name": "Heat Treatment", "machine": None, "outsourced": True, "turnaround_days": 3},
    # Not a shop process; kept inactive so completed history still displays.
    {"code": "CHECKING", "name": "Checking", "machine": None, "active": False},
    {"code": "WELDING", "name": "Welding", "machine": None},
    # The shop's cutting is laser cutting.
    {"code": "CUTTING", "name": "Cutting", "machine": "LASER"},
    {"code": "BENDING", "name": "Bending", "machine": "BENDING"},
    {"code": "FORMING", "name": "Forming", "machine": None},
    {"code": "ASSEMBLY", "name": "Assembly", "machine": None},
    {"code": "FINISHING", "name": "Finishing (Bapping)", "machine": None},
    {"code": "LAYOUT", "name": "Layout", "machine": None},
    {"code": "FITTING", "name": "Fitting", "machine": None},
]

# The final operation: it stays last on a job, and operations added later go before it.
FINAL_OPERATION_CODE = "FINISHING"

SKILL_TOKEN_TO_MACHINE = {
    "lathe": "LATHE",
    "milling": "MILLING",
    "grinding": "GRINDING",
    "drilling": "DRILLING",
    "shaper": "SHAPER",
    "bending": "BENDING",
    "laser": "LASER",
    "lathe machine": "LATHE",
    "milling machine": "MILLING",
    "grinding machine": "GRINDING",
    "drilling machine": "DRILLING",
    "shaper machine": "SHAPER",
    "bending machine": "BENDING",
    "laser machine": "LASER",
}
