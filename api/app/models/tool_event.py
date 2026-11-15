import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


def _num(v):
    if v is None:
        return None
    return float(v)


class ToolEventType(enum.Enum):
    BORROW = "BORROW"
    RETURN = "RETURN"
    ISSUE = "ISSUE"
    ADJUST = "ADJUST"
    RECEIVE = "RECEIVE"
    SCAN = "SCAN"
    UNDO = "UNDO"
    CORRECTION = "CORRECTION"


class ToolEvent(db.Model):
    __tablename__ = "tool_events"
    __table_args__ = (
        db.Index("ix_tool_event_tool_created", "tool_id", "created_at"),
        # Partial unique: one offline client action id → one event (migration 052).
        db.Index(
            "uq_tool_events_client_action_id",
            "client_action_id",
            unique=True,
            postgresql_where=db.text("client_action_id IS NOT NULL"),
            sqlite_where=db.text("client_action_id IS NOT NULL"),
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    # Consumable stock ADJUST / RECEIVE / SCAN / UNDO / CORRECTION (and legacy rows)
    tool_id = db.Column(
        db.String(36), db.ForeignKey("tools.id"), nullable=True, index=True
    )
    # Individually tracked tool BORROW / RETURN / RECEIVE
    tool_unit_id = db.Column(
        db.String(36), db.ForeignKey("tool_units.id"), nullable=True, index=True
    )
    worker_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    type = db.Column(db.Enum(ToolEventType), nullable=False)
    quantity = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("1"))
    # Signed effect on quantity_on_hand (consumable movements). Null on borrow/return.
    stock_delta = db.Column(db.Numeric(12, 2), nullable=True)
    reason = db.Column(db.String(255), nullable=True)
    supplier = db.Column(db.String(255), nullable=True)
    received_on = db.Column(db.Date, nullable=True)
    job_order_id = db.Column(
        db.String(36), db.ForeignKey("job_orders.id"), nullable=True
    )
    operation_id = db.Column(
        db.String(36),
        db.ForeignKey("operations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # RECEIVE of a consumable line on a supplier order; NULL = Receive delivery
    # without a PO (e.g. a walk-in purchase).
    material_purchase_id = db.Column(
        db.String(36),
        db.ForeignKey("material_purchases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # ISSUE: the Office Staff who handed the consumable to ``worker_id``.
    recorded_by_id = db.Column(
        db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    undoes_event_id = db.Column(
        db.String(36),
        db.ForeignKey("tool_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    undone_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Offline / retry idempotency for consumable scans (partial unique when set).
    client_action_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    tool = db.relationship("Tool", back_populates="events")
    tool_unit = db.relationship("ToolUnit", back_populates="events")
    worker = db.relationship("User", back_populates="tool_events", foreign_keys=[worker_id])
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])
    job_order = db.relationship("JobOrder", back_populates="tool_events")
    operation = db.relationship("JobOperation", foreign_keys=[operation_id])
    material_purchase = db.relationship("MaterialPurchase")
    undoes_event = db.relationship("ToolEvent", remote_side=[id], foreign_keys=[undoes_event_id])

    def to_dict(self):
        unit = self.tool_unit
        tool = self.tool
        return {
            "id": self.id,
            "toolId": self.tool_id,
            "toolUnitId": self.tool_unit_id,
            "toolName": (
                unit.tool_type.name
                if unit and unit.tool_type
                else (tool.name if tool else None)
            ),
            "toolCode": tool.code if tool else None,
            "toolUnit": tool.unit if tool else None,
            "assetCode": unit.asset_code if unit else None,
            "toolCategory": (
                "TOOL_UNIT"
                if unit
                else (tool.category.value if tool and tool.category else None)
            ),
            "toolSizeSpec": tool.size_spec if tool else None,
            "quantityOnHandAfter": _num(tool.quantity_on_hand) if tool else None,
            "unitStatus": unit.status.value if unit and unit.status else None,
            "currentHolderName": (
                unit.current_holder.full_name
                if unit and unit.current_holder
                else None
            ),
            "workerId": self.worker_id,
            "workerName": self.worker.full_name if self.worker else None,
            "type": self.type.value,
            "quantity": _num(self.quantity),
            "stockDelta": _num(self.stock_delta),
            "reason": self.reason,
            "supplier": self.supplier,
            "receivedOn": self.received_on.isoformat() if self.received_on else None,
            "jobOrderId": self.job_order_id,
            "operationId": self.operation_id,
            "recordedByName": self.recorded_by.full_name if self.recorded_by else None,
            "undoesEventId": self.undoes_event_id,
            "undoneAt": self.undone_at.isoformat() if self.undone_at else None,
            "clientActionId": self.client_action_id,
            "supplierOrderId": (
                self.material_purchase.supplier_order_id if self.material_purchase else None
            ),
            "poNumber": (
                self.material_purchase.supplier_order.po_number
                if self.material_purchase and self.material_purchase.supplier_order
                else None
            ),
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }
