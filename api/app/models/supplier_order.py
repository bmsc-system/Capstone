"""Supplier purchase order grouping job material lines from one or more job
orders and consumable restock lines."""

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db

PO_PREFIX = "BMSC-PO-"


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


def format_po_number(supplier_code: str, year: int, seq: int) -> str:
    """Supplier code + 2-digit year + 6-digit sequence, e.g. STP26000001.
    The sequence restarts each year for each supplier."""
    return f"{supplier_code}{year % 100:02d}{seq:06d}"


def format_legacy_po_number(seq: int) -> str:
    """The earlier shop-wide format (BMSC-PO-00012), kept by existing orders."""
    return f"{PO_PREFIX}{seq:05d}"


DEFAULT_TERMS_OF_PAYMENT = "PDC"
DELIVERY_MODES = ("PICKUP", "DELIVERY")
DEFAULT_DELIVERY_MODE = "DELIVERY"


class SupplierOrderStatus(enum.Enum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


# Lines on these orders count as ordered for the job (material status, readiness).
PLACED_ORDER_STATUSES = (
    SupplierOrderStatus.ISSUED,
    SupplierOrderStatus.PARTIALLY_RECEIVED,
    SupplierOrderStatus.RECEIVED,
)


class SupplierOrder(db.Model):
    __tablename__ = "supplier_orders"
    __table_args__ = (
        # At most one open draft per supplier; "Order materials" adds to it.
        db.Index(
            "uq_supplier_orders_one_draft",
            "supplier_id",
            unique=True,
            postgresql_where=db.text("status = 'DRAFT'"),
        ),
        # New-format numbers: one sequence per supplier per year. Orders issued
        # before it have no po_year and keep their shop-wide po_seq.
        db.Index(
            "uq_supplier_orders_supplier_year_seq",
            "supplier_id",
            "po_year",
            "po_seq",
            unique=True,
            postgresql_where=db.text("po_year IS NOT NULL"),
        ),
        db.CheckConstraint(
            "delivery_mode IN ('PICKUP', 'DELIVERY')", name="ck_supplier_orders_delivery_mode"
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    po_seq = db.Column(db.Integer, nullable=True)
    po_year = db.Column(db.SmallInteger, nullable=True)
    po_number = db.Column(db.String(32), nullable=True, unique=True)
    supplier_id = db.Column(
        db.String(36), db.ForeignKey("suppliers.id"), nullable=False, index=True
    )
    status = db.Column(
        db.Enum(SupplierOrderStatus),
        nullable=False,
        default=SupplierOrderStatus.DRAFT,
        index=True,
    )
    date_issued = db.Column(db.Date, nullable=True)
    expected_delivery_date = db.Column(db.Date, nullable=True)
    # Expected date at issue, kept the first time Office Staff change it.
    original_expected_delivery_date = db.Column(db.Date, nullable=True)
    expected_delivery_note = db.Column(db.Text, nullable=True)
    # Date the last line arrived (set when the order becomes RECEIVED).
    received_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    vat_rate = db.Column(db.Numeric(5, 2), nullable=True)
    terms_of_payment = db.Column(
        db.String(100), nullable=False, default=DEFAULT_TERMS_OF_PAYMENT,
        server_default=DEFAULT_TERMS_OF_PAYMENT,
    )
    delivery_mode = db.Column(
        db.String(16), nullable=False, default=DEFAULT_DELIVERY_MODE,
        server_default=DEFAULT_DELIVERY_MODE,
    )
    prepared_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    issued_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)
    # First print of the issued PO (the supplier needs the paper copy before receive).
    printed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    printed_by_id = db.Column(
        db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    print_count = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    supplier = db.relationship("Supplier")
    prepared_by = db.relationship("User", foreign_keys=[prepared_by_id])
    issued_by = db.relationship("User", foreign_keys=[issued_by_id])
    printed_by = db.relationship("User", foreign_keys=[printed_by_id])
    lines = db.relationship(
        "MaterialPurchase",
        back_populates="supplier_order",
        order_by="MaterialPurchase.created_at",
    )

    @property
    def active_lines(self):
        return [ln for ln in self.lines or [] if ln.cancelled_at is None]

    def days_overdue(self, today=None) -> int:
        """Most days late among lines still awaited past the expected date."""
        return max((ln.days_overdue(today) for ln in self.lines or []), default=0)

    @property
    def subtotal(self) -> Decimal:
        return sum((ln.line_total for ln in self.active_lines), Decimal("0"))

    def to_dict(self, include_lines=False):
        s = self.supplier
        data = {
            "id": self.id,
            "poNumber": self.po_number,
            "supplierId": self.supplier_id,
            "supplierName": s.name if s else None,
            "supplierLeadTimeDays": s.typical_lead_time_days if s else None,
            # Printed under the supplier name on the PO.
            "supplierAddress": s.address if s else None,
            "supplierContactPerson": s.contact_person if s else None,
            "supplierPhone": s.phone if s else None,
            "status": self.status.value,
            "dateIssued": self.date_issued.isoformat() if self.date_issued else None,
            "expectedDeliveryDate": (
                self.expected_delivery_date.isoformat()
                if self.expected_delivery_date
                else None
            ),
            "originalExpectedDeliveryDate": (
                self.original_expected_delivery_date.isoformat()
                if self.original_expected_delivery_date
                else None
            ),
            "expectedDeliveryNote": self.expected_delivery_note,
            "receivedDate": self.received_date.isoformat() if self.received_date else None,
            "notes": self.notes,
            "vatRate": float(self.vat_rate) if self.vat_rate is not None else None,
            "termsOfPayment": self.terms_of_payment or DEFAULT_TERMS_OF_PAYMENT,
            "deliveryMode": self.delivery_mode or DEFAULT_DELIVERY_MODE,
            "preparedById": self.prepared_by_id,
            "preparedByName": self.prepared_by.full_name if self.prepared_by else None,
            "issuedById": self.issued_by_id,
            "issuedByName": self.issued_by.full_name if self.issued_by else None,
            "printedAt": self.printed_at.isoformat() if self.printed_at else None,
            "printedById": self.printed_by_id,
            "printedByName": self.printed_by.full_name if self.printed_by else None,
            "printCount": int(self.print_count or 0),
            "lineCount": len(self.active_lines),
            "jobCount": len({ln.job_order_id for ln in self.active_lines if ln.job_order_id}),
            "consumableLineCount": sum(1 for ln in self.active_lines if ln.tool_id),
            "subtotal": float(self.subtotal),
            "daysOverdue": self.days_overdue(),
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }
        if include_lines:
            data["lines"] = [ln.to_dict() for ln in self.lines or []]
            if self.status == SupplierOrderStatus.DRAFT:
                data.update(self.next_po_number_preview())
        return data

    def next_po_number_preview(self, year=None) -> dict:
        """The PO number a draft would get if issued now (the supplier's next
        number this year). Office Staff may change it when issuing."""
        from app.services.schedule_calendar import shop_now

        s = self.supplier
        year = int(year or shop_now().year)
        current = (
            db.session.query(db.func.max(SupplierOrder.po_seq))
            .filter(SupplierOrder.supplier_id == self.supplier_id, SupplierOrder.po_year == year)
            .scalar()
        )
        seq = int(current or 0) + 1
        code = s.code if s else None
        return {
            "supplierCode": code,
            "nextPoYear": year,
            "nextPoSeq": seq,
            "nextPoNumber": format_po_number(code, year, seq) if code else None,
        }
