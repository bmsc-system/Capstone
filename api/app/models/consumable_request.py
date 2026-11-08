"""A consumable reported as running low (or out) by someone on the shop floor.

Consumables are not counted, so the people who use them are the first to know
when an item is running out. A Production Worker taps "Running low" or "Out"
on the item; the Office Staff see it on their dashboard and the Consumables
tab, and recording a purchase of the item (or receiving it on a supplier order)
closes the report. One open report per item: a second tap updates it.
"""

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class ConsumableRequestLevel:
    LOW = "LOW"
    OUT = "OUT"
    ALL = (LOW, OUT)


class ConsumableRequestStatus:
    OPEN = "OPEN"
    BOUGHT = "BOUGHT"  # closed by a recorded purchase or a received supplier order line
    DISMISSED = "DISMISSED"  # closed by the Office Staff without buying
    WITHDRAWN = "WITHDRAWN"  # taken back by the person who reported it


class ConsumableRequest(db.Model):
    __tablename__ = "consumable_requests"
    __table_args__ = (
        db.Index("ix_consumable_requests_tool_status", "tool_id", "status"),
        # At most one open report per item, even when two taps arrive at once.
        db.Index(
            "uq_consumable_requests_one_open",
            "tool_id",
            unique=True,
            postgresql_where=db.text("status = 'OPEN'"),
            sqlite_where=db.text("status = 'OPEN'"),
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    tool_id = db.Column(db.String(36), db.ForeignKey("tools.id"), nullable=False)
    level = db.Column(db.String(8), nullable=False, default=ConsumableRequestLevel.LOW)
    status = db.Column(db.String(12), nullable=False, default=ConsumableRequestStatus.OPEN)
    reported_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    reported_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    closed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    closed_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)
    consumable_purchase_id = db.Column(
        db.String(36), db.ForeignKey("consumable_purchases.id"), nullable=True
    )
    material_purchase_id = db.Column(
        db.String(36), db.ForeignKey("material_purchases.id"), nullable=True
    )

    tool = db.relationship("Tool")
    reported_by = db.relationship("User", foreign_keys=[reported_by_id])
    closed_by = db.relationship("User", foreign_keys=[closed_by_id])

    def to_dict(self, viewer_id=None):
        reporter = self.reported_by
        return {
            "id": self.id,
            "toolId": self.tool_id,
            "name": self.tool.name if self.tool else None,
            "level": self.level,
            "status": self.status,
            "reportedById": self.reported_by_id,
            "reportedByName": (reporter.nickname or reporter.full_name) if reporter else None,
            "reportedAt": self.reported_at.isoformat() if self.reported_at else None,
            "closedAt": self.closed_at.isoformat() if self.closed_at else None,
            "mine": viewer_id is not None and viewer_id == self.reported_by_id,
        }
