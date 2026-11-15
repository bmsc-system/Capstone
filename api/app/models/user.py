import enum
import uuid
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class UserRole(enum.Enum):
    ADMIN = "ADMIN"
    OFFICE_STAFF = "OFFICE_STAFF"
    PRODUCTION_WORKER = "PRODUCTION_WORKER"


class UserStatus(enum.Enum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    # Optional until the Admin invites the person (imported employees have none).
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    mobile_number = db.Column(db.String(16), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    full_name = db.Column(db.String(255), nullable=False)
    nickname = db.Column(db.String(40), nullable=True)
    photo_updated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    role = db.Column(db.Enum(UserRole), nullable=False, index=True)
    status = db.Column(
        db.Enum(UserStatus),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default="ACTIVE",
        index=True,
    )
    # Kept in sync with status for older call sites that filter on active.
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    worker_profile = db.relationship(
        "WorkerProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    skills = db.relationship(
        "WorkerSkill",
        back_populates="worker",
        cascade="all, delete-orphan",
        order_by="WorkerSkill.is_primary.desc()",
    )
    schedules = db.relationship(
        "WorkerSchedule",
        back_populates="worker",
        cascade="all, delete-orphan",
        order_by="WorkerSchedule.day_of_week",
    )
    created_job_orders = db.relationship(
        "JobOrder", back_populates="created_by", foreign_keys="JobOrder.created_by_id"
    )
    assigned_operations = db.relationship(
        "JobOperation",
        back_populates="assigned_worker",
        foreign_keys="JobOperation.assigned_worker_id",
    )
    tool_events = db.relationship(
        "ToolEvent", back_populates="worker", foreign_keys="ToolEvent.worker_id"
    )

    def sync_active_flag(self):
        self.active = self.status == UserStatus.ACTIVE

    @property
    def photo_version(self):
        """Changes whenever the photo does; None when there is no photo."""
        return int(self.photo_updated_at.timestamp()) if self.photo_updated_at else None

    def to_dict(self, include_profile=False, include_skills=False, include_schedule=False):
        data = {
            "id": self.id,
            "email": self.email,
            "mobileNumber": self.mobile_number,
            "fullName": self.full_name,
            "nickname": self.nickname,
            "photoVersion": self.photo_version,
            "role": self.role.value,
            "status": self.status.value if self.status else UserStatus.ACTIVE.value,
            "active": self.active,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }
        if include_profile and self.worker_profile:
            data["workerProfile"] = self.worker_profile.to_dict()
        elif include_profile and self.role in (
            UserRole.PRODUCTION_WORKER,
            UserRole.ADMIN,
        ):
            data["workerProfile"] = {
                "id": None,
                "userId": self.id,
                "skills": [s.to_dict() for s in (self.skills or [])],
                "fullName": self.full_name,
                "email": self.email,
            }
        if include_skills:
            data["skills"] = [s.to_dict() for s in (self.skills or [])]
        if include_schedule:
            data["schedules"] = [s.to_dict() for s in (self.schedules or [])]
        return data


class UserPhoto(db.Model):
    """A user's 128x128 JPEG, kept in the database (the host's disk is wiped on deploy)."""

    __tablename__ = "user_photos"

    user_id = db.Column(
        db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    data = db.Column(db.LargeBinary, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
