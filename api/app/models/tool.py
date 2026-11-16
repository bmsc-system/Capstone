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


class ToolCategory(enum.Enum):
    """Legacy RETURNABLE_TOOL kept for DB enum compatibility; new stock is CONSUMABLE only."""

    RETURNABLE_TOOL = "RETURNABLE_TOOL"
    CONSUMABLE = "CONSUMABLE"


class Tool(db.Model):
    """
    Consumable inventory item (stocktake model).
    Individually tracked tools live in tool_types / tool_units.
    """

    __tablename__ = "tools"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    name = db.Column(db.String(255), nullable=False, index=True)
    code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    category = db.Column(
        db.Enum(ToolCategory),
        nullable=False,
        default=ToolCategory.CONSUMABLE,
        index=True,
    )
    unit = db.Column(db.String(32), nullable=False, default="pcs")
    # Previous unit when remapped to a countable container (display / Office review).
    legacy_unit = db.Column(db.String(32), nullable=True)
    unit_needs_review = db.Column(db.Boolean, nullable=False, default=False)
    # Fallback unit cost for ABC when no received PO price exists.
    unit_cost = db.Column(db.Numeric(14, 4), nullable=True)
    quantity_on_hand = db.Column(
        db.Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    # Kept for history; reorder triggers use computed ROP, not this field.
    minimum_stock = db.Column(db.Numeric(12, 2), nullable=True)
    size_spec = db.Column(db.String(64), nullable=True)
    shop_term = db.Column(db.String(100), nullable=True)
    # In use: listed and orderable. Off means archived: hidden from pickers and lists, kept on past records.
    active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.text("true")
    )
    # Most consumables are bought in cash at nearby stores; only these are
    # offered on a supplier purchase order (cutting oil, by the gallon).
    ordered_from_supplier = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)

    events = db.relationship(
        "ToolEvent",
        back_populates="tool",
        order_by="ToolEvent.created_at.desc()",
    )

    @property
    def low_stock(self) -> bool:
        if self.minimum_stock is None:
            return False
        return Decimal(str(self.quantity_on_hand or 0)) <= Decimal(
            str(self.minimum_stock)
        )

    @property
    def is_stocked(self) -> bool:
        """True once the item has had stock: on hand, any stock event, or a PO line.
        A catalog item with none of these is "not stocked yet"."""
        if Decimal(str(self.quantity_on_hand or 0)) > 0:
            return True
        if self.id is None:
            return False
        from app.models.material_purchase import MaterialPurchase
        from app.models.tool_event import ToolEvent

        if db.session.query(ToolEvent.id).filter(ToolEvent.tool_id == self.id).first():
            return True
        return (
            db.session.query(MaterialPurchase.id)
            .filter(MaterialPurchase.tool_id == self.id)
            .first()
            is not None
        )

    def to_dict(self, include_custody=False, worker_id=None):
        data = {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "category": self.category.value if self.category else None,
            "unit": self.unit,
            "legacyUnit": self.legacy_unit,
            "unitNeedsReview": bool(self.unit_needs_review),
            "unitCost": _num(self.unit_cost),
            "quantityOnHand": _num(self.quantity_on_hand),
            "minimumStock": _num(self.minimum_stock),
            "sizeSpec": self.size_spec,
            "shopTerm": self.shop_term,
            "active": bool(self.active) if self.active is not None else True,
            "orderedFromSupplier": bool(self.ordered_from_supplier),
            "lowStock": self.low_stock,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }
        if include_custody:
            data["holders"] = []
            data["custody"] = None
            if worker_id:
                data["myOutstanding"] = 0
        return data
