import enum
import re
import uuid
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())



_TECHNICAL_ERROR = re.compile(
    r"\[Errno|Connection (refused|reset)|timed out|Name or service not known|getaddrinfo|SMTP|Traceback",
    re.IGNORECASE,
)


_OLD_WORDING = {
    "Notifications are turned off (NOTIFICATIONS_ENABLED is false)": "Email sending is turned off on this server",
    "Recipient is not on NOTIFY_ALLOWLIST": "This server only sends email to approved test addresses",
}


def readable_send_error(message: str | None) -> str | None:
    """Older rows kept the raw technical error; show those in plain words."""
    if message in _OLD_WORDING:
        return _OLD_WORDING[message]
    if message and _TECHNICAL_ERROR.search(message):
        return "The mail server could not be reached. Try sending again later."
    return message

class NotificationMilestone(enum.Enum):
    """Client job updates. JOB_DELIVERED is the "ready for delivery" email.
    JOB_COMPLETED is no longer sent (Ready for delivery says the job is done);
    it stays for older log rows."""

    JOB_RECEIVED = "JOB_RECEIVED"
    JOB_STARTED = "JOB_STARTED"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_DELIVERED = "JOB_DELIVERED"
    JOB_HALFWAY = "JOB_HALFWAY"
    JOB_FINAL_STAGE = "JOB_FINAL_STAGE"
    JOB_DELAYED = "JOB_DELAYED"


class NotificationChannel(enum.Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"


class NotificationStatus(enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NOT_SENT = "NOT_SENT"


class NotificationEvent:
    """Values of NotificationLog.event. Job events reuse the milestone names."""

    INVITATION = "INVITATION"
    PASSWORD_RESET = "PASSWORD_RESET"
    TEST = "TEST"


class NotificationLog(db.Model):
    """One row per email/SMS send attempt (sent, failed or skipped)."""

    __tablename__ = "notification_logs"
    __table_args__ = (
        db.Index(
            "ix_notification_job_milestone_channel",
            "job_order_id",
            "milestone",
            "channel",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    job_order_id = db.Column(
        db.String(36), db.ForeignKey("job_orders.id"), nullable=True, index=True
    )
    client_id = db.Column(
        db.String(36), db.ForeignKey("clients.id"), nullable=True, index=True
    )
    # Set for client job-status updates only; drives the once-per-job rule.
    milestone = db.Column(db.Enum(NotificationMilestone), nullable=True, index=True)
    event = db.Column(db.String(40), nullable=False, index=True)
    channel = db.Column(db.Enum(NotificationChannel), nullable=False)
    recipient = db.Column(db.String(255), nullable=False)
    message_body = db.Column(db.Text, nullable=False)
    status = db.Column(db.Enum(NotificationStatus), nullable=False, index=True)
    provider_reference = db.Column(db.String(255), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    job_order = db.relationship("JobOrder", back_populates="notification_logs")
    client = db.relationship("Client", back_populates="notification_logs")

    def __init__(self, **kwargs):
        if not kwargs.get("event") and kwargs.get("milestone") is not None:
            kwargs["event"] = kwargs["milestone"].value
        super().__init__(**kwargs)

    def to_dict(self):
        job = self.job_order
        return {
            "id": self.id,
            "jobOrderId": self.job_order_id,
            "jobNumber": job.job_number if job else None,
            "jobTitle": job.title if job else None,
            "clientId": self.client_id,
            "clientName": self.client.name if self.client else None,
            "milestone": self.milestone.value if self.milestone else None,
            "event": self.event,
            "channel": self.channel.value if self.channel else None,
            "recipient": self.recipient,
            "messageBody": self.message_body,
            "status": self.status.value if self.status else None,
            "providerReference": self.provider_reference,
            "errorMessage": readable_send_error(self.error_message),
            "sentAt": self.sent_at.isoformat() if self.sent_at else None,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }
