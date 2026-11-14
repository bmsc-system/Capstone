import enum
import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import validates

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


# Colors given to job orders on the schedule board so each job stands apart.
# Jobs made one after another get very different colors. Red and amber are
# left out because the board uses them for late, clashing, stalled and waiting
# work. White bar text stays readable on all of them.
AUTO_SCHEDULE_COLORS = (
    "#2563EB",  # blue
    "#DB2777",  # pink
    "#4D7C0F",  # green
    "#7C3AED",  # violet
    "#0F766E",  # teal
    "#92400E",  # brown
    "#0891B2",  # cyan
    "#475569",  # slate
)


def fallback_schedule_color(job_id) -> str:
    """Steady color for a job saved before colors were given out."""
    digest = hashlib.md5(str(job_id or "").encode()).hexdigest()
    return AUTO_SCHEDULE_COLORS[int(digest, 16) % len(AUTO_SCHEDULE_COLORS)]


class JobOrderStatus(enum.Enum):
    # Pre-production (Office / Admin planning)
    DRAFT = "DRAFT"
    # Production floor
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    # Completed, invoice recorded, waiting to go out (set by the Admin).
    FOR_DELIVERY = "FOR_DELIVERY"
    # Handed over to the client; delivered_at is the date on-time is measured on.
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    # Legacy labels — retained for Postgres enum compatibility only; never set in code.
    PLANNING = "PLANNING"
    RELEASED = "RELEASED"
    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"


# Enum labels that remain in the DB type but must not be written by application code.
DEPRECATED_JOB_STATUSES = frozenset(
    {
        JobOrderStatus.PLANNING,
        JobOrderStatus.RELEASED,
        JobOrderStatus.UNASSIGNED,
        JobOrderStatus.ASSIGNED,
    }
)

PRODUCTION_STATUSES = frozenset(
    {
        JobOrderStatus.SCHEDULED,
        JobOrderStatus.IN_PROGRESS,
        JobOrderStatus.COMPLETED,
        JobOrderStatus.FOR_DELIVERY,
        JobOrderStatus.DELIVERED,
    }
)

# Production finished: Completed, For Delivery or Delivered.
DONE_STATUSES = frozenset(
    {
        JobOrderStatus.COMPLETED,
        JobOrderStatus.FOR_DELIVERY,
        JobOrderStatus.DELIVERED,
    }
)

# Default job-order list (non-archived): production plus cancelled history.
LIST_STATUSES = PRODUCTION_STATUSES | {JobOrderStatus.CANCELLED}

# Workers may only see committed production jobs (not drafts or cancelled).
PRODUCTION_VISIBLE_STATUSES = PRODUCTION_STATUSES

# Excluded from schedule board, worker/machine booking, and open capacity.
INACTIVE_SCHEDULE_STATUSES = frozenset(
    {JobOrderStatus.DRAFT, JobOrderStatus.CANCELLED}
)

CANCEL_REASONS = frozenset(
    {
        "CLIENT_CANCELLED",
        "DUPLICATE_OR_ENCODING_ERROR",
        "OTHER",
    }
)

ARCHIVEABLE_STATUSES = frozenset(
    {
        JobOrderStatus.COMPLETED,
        JobOrderStatus.FOR_DELIVERY,
        JobOrderStatus.DELIVERED,
        JobOrderStatus.CANCELLED,
    }
)


class JobPriority(enum.Enum):
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"


class JobType(enum.Enum):
    FABRICATION = "FABRICATION"
    MACHINING = "MACHINING"
    REPAIR = "REPAIR"


class MaterialStatus(enum.Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    TO_ORDER = "TO_ORDER"
    ORDERED = "ORDERED"
    RECEIVED = "RECEIVED"


def default_material_status(job_type: JobType) -> MaterialStatus:
    """Repair works on the client's part; fabrication and machining need stock ordered."""
    if job_type == JobType.REPAIR:
        return MaterialStatus.NOT_REQUIRED
    return MaterialStatus.TO_ORDER


class PartCondition(enum.Enum):
    RAW_MATERIAL = "RAW_MATERIAL"
    CLIENT_SUPPLIED_ITEM = "CLIENT_SUPPLIED_ITEM"
    BLANK = "BLANK"
    WORK_IN_PROCESS = "WORK_IN_PROCESS"
    MACHINED = "MACHINED"
    HEAT_TREATED = "HEAT_TREATED"
    FINISHED = "FINISHED"
    CUT = "CUT"
    FORMED = "FORMED"
    ASSEMBLED = "ASSEMBLED"


def draft_stage_label(job: "JobOrder") -> str:
    """Human-readable planning stage for DRAFT rows (not stored)."""
    ops = list(job.operations or [])
    meaningful = [
        o
        for o in ops
        if (o.operation_name or "").strip() or o.operation_type_id
    ]
    if not meaningful:
        return "No operations yet"
    n = len(meaningful)
    word = "operation" if n == 1 else "operations"
    if all(o.scheduled_start and o.scheduled_end for o in meaningful):
        return f"{n} {word}, schedule ready"
    return f"{n} {word}, not scheduled"


class JobOrder(db.Model):
    __tablename__ = "job_orders"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    client_id = db.Column(
        db.String(36), db.ForeignKey("clients.id"), nullable=False, index=True
    )
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    # Client PO "date required" — reused; do not add a separate date_required column.
    due_date = db.Column(db.Date, nullable=False, index=True)
    client_po_number = db.Column(db.String(100), nullable=True)
    po_date = db.Column(db.Date, nullable=True)
    status = db.Column(
        db.Enum(JobOrderStatus), nullable=False, default=JobOrderStatus.DRAFT, index=True
    )
    priority = db.Column(
        db.Enum(JobPriority), nullable=False, default=JobPriority.MODERATE, index=True
    )
    job_type = db.Column(
        db.Enum(JobType), nullable=False, default=JobType.FABRICATION, index=True
    )
    part_condition = db.Column(
        db.Enum(PartCondition),
        nullable=False,
        default=PartCondition.RAW_MATERIAL,
        index=True,
    )
    quantity = db.Column(db.Numeric(12, 2), nullable=True)
    unit_of_measure = db.Column(db.String(32), nullable=True)
    amount = db.Column(db.Numeric(14, 2), nullable=True)
    # Planned materials from the earlier job order form, kept read-only
    # ("Planned (earlier record)"). Materials are now entered when ordering.
    # [{ "name": "Mild steel plate", "quantity": 2, "unit": "pcs" }, ...]
    raw_materials = db.Column(JSONB, nullable=False, default=list)
    material_status = db.Column(
        db.Enum(MaterialStatus),
        nullable=False,
        default=MaterialStatus.TO_ORDER,
        index=True,
    )
    material_expected_date = db.Column(db.Date, nullable=True)
    material_received_date = db.Column(db.Date, nullable=True)
    # Preferred supplier for the job (optional); PO/invoice number stays free-text.
    supplier_id = db.Column(
        db.String(36),
        db.ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Supplier's own order / invoice number.
    supplier_reference = db.Column(db.String(120), nullable=True)
    # Material delay: first operation's planned start before the first automatic
    # move (set once, never overwritten), plus the latest move's reason and PO.
    material_delay_original_start = db.Column(db.DateTime(timezone=True), nullable=True)
    material_delay_reason = db.Column(db.Text, nullable=True)
    material_delay_supplier_order_id = db.Column(
        db.String(36),
        db.ForeignKey("supplier_orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    material_delayed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # DelayKind of the latest move (MATERIAL or RESCHEDULED).
    delay_kind = db.Column(db.String(20), nullable=True)
    # Hex color (#RRGGBB) for schedule board distinction; optional.
    schedule_color = db.Column(db.String(7), nullable=True)
    # Staff were told the job is at risk of missing its required date; cleared
    # once it is back on time, so the next change raises a new alert.
    at_risk_alerted = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
    # Set when a change (such as a redo) could not be scheduled automatically;
    # the Admin re-plans the job, which clears it.
    needs_replan_reason = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False
    )
    delivered_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # When the Admin set the job For Delivery.
    for_delivery_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # The latest finish date the client was told (Received or Delayed email);
    # a Delayed email goes out only when the scheduled finish moves past it.
    client_notified_finish = db.Column(db.Date, nullable=True)
    # When the Admin confirmed the schedule; worker actions cannot predate it.
    released_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancelled_by_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    cancel_reason = db.Column(db.String(40), nullable=True)
    cancel_note = db.Column(db.Text, nullable=True)
    archived_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    archived_by_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    client = db.relationship("Client", back_populates="job_orders")
    supplier = db.relationship("Supplier", back_populates="job_orders")
    created_by = db.relationship(
        "User", back_populates="created_job_orders", foreign_keys=[created_by_id]
    )
    cancelled_by = db.relationship("User", foreign_keys=[cancelled_by_id])
    archived_by = db.relationship("User", foreign_keys=[archived_by_id])
    operations = db.relationship(
        "JobOperation",
        back_populates="job_order",
        cascade="all, delete-orphan",
        order_by="JobOperation.sequence_no",
    )
    material_purchases = db.relationship(
        "MaterialPurchase",
        back_populates="job_order",
        cascade="all, delete-orphan",
        order_by="MaterialPurchase.date_ordered",
    )
    sales_invoice = db.relationship(
        "SalesInvoice", back_populates="job_order", uselist=False
    )
    material_delay_supplier_order = db.relationship(
        "SupplierOrder", foreign_keys=[material_delay_supplier_order_id]
    )
    tool_events = db.relationship("ToolEvent", back_populates="job_order")
    notification_logs = db.relationship(
        "NotificationLog",
        back_populates="job_order",
        cascade="all, delete-orphan",
    )

    def _serialize_operations(self, ops):
        """Serialize ops with request-scoped schedule/downtime caches (no N+1)."""
        from app.models.operation_time import MachineDowntime
        from app.services.schedule_calendar import (
            load_calendar_exceptions,
            load_worker_schedule_maps_many,
            utc_to_shop,
        )

        worker_ids = {wid for op in ops for wid in op.crew_ids}
        schedule_by_worker = load_worker_schedule_maps_many(worker_ids)

        starts = [op.scheduled_start for op in ops if op.scheduled_start]
        ends = [op.scheduled_end for op in ops if op.scheduled_end]
        if starts and ends:
            calendar_exceptions = load_calendar_exceptions(
                utc_to_shop(min(starts)).date(),
                utc_to_shop(max(ends)).date(),
            )
        else:
            calendar_exceptions = {}

        unit_ids = {op.machine_unit_id for op in ops if op.machine_unit_id}
        if unit_ids:
            open_downtime_unit_ids = {
                uid
                for (uid,) in db.session.query(MachineDowntime.machine_unit_id)
                .filter(
                    MachineDowntime.machine_unit_id.in_(unit_ids),
                    MachineDowntime.ended_at.is_(None),
                )
                .all()
            }
        else:
            open_downtime_unit_ids = set()

        return [
            op.to_dict(
                schedule_by_worker=schedule_by_worker,
                calendar_exceptions=calendar_exceptions,
                open_downtime_unit_ids=open_downtime_unit_ids,
            )
            for op in ops
        ]

    @validates("raw_materials")
    def _ensure_raw_material_ids(self, _key, items):
        """Every planned material carries a stable id that purchases link to."""
        out = []
        for item in items or []:
            if isinstance(item, dict) and not item.get("id"):
                item = {**item, "id": _uuid()}
            out.append(item)
        return out

    def planned_materials_summary(self):
        """Planned quantity vs linked purchase lines for each planned material.

        Ordered = placed lines (issued PO or recorded without a PO); draft =
        lines still on a draft PO. Cancelled lines count for nothing, so their
        material goes back to to-order. Still to order = planned - ordered - draft.
        """
        ordered: dict[str, Decimal] = {}
        drafted: dict[str, Decimal] = {}
        for p in self.material_purchases or []:
            if not p.planned_material_id or p.cancelled_at is not None:
                continue
            bucket = drafted if p.is_draft else ordered
            bucket[p.planned_material_id] = bucket.get(
                p.planned_material_id, Decimal("0")
            ) + Decimal(str(p.quantity or 0))
        rows = []
        for item in self.raw_materials or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            purchased = ordered.get(item["id"], Decimal("0"))
            draft = drafted.get(item["id"], Decimal("0"))
            planned = item.get("quantity")
            if item.get("fromStock"):
                remaining = Decimal("0")
                status = "FROM_STOCK"
            elif planned is None:
                remaining = None
                if purchased > 0:
                    status = "PURCHASED"
                elif draft > 0:
                    status = "ON_DRAFT_ORDER"
                else:
                    status = "TO_ORDER"
            else:
                remaining = max(Decimal(str(planned)) - purchased - draft, Decimal("0"))
                if purchased >= Decimal(str(planned)):
                    status = "PURCHASED"
                elif remaining == 0:
                    status = "ON_DRAFT_ORDER"
                elif purchased > 0 or draft > 0:
                    status = "PARTLY_ORDERED"
                else:
                    status = "TO_ORDER"
            rows.append(
                {
                    "id": item["id"],
                    "name": item.get("name"),
                    "unit": item.get("unit"),
                    "plannedQuantity": planned,
                    "purchasedQuantity": float(purchased),
                    "draftQuantity": float(draft),
                    "remainingQuantity": float(remaining) if remaining is not None else None,
                    "fromStock": bool(item.get("fromStock")),
                    "materialCatalogId": item.get("materialCatalogId"),
                    "gradeOrSpec": item.get("gradeOrSpec"),
                    "sizeSpec": item.get("sizeSpec"),
                    "status": status,
                }
            )
        return rows

    def _material_readiness(self):
        from app.services.material_purchase_service import (
            derived_material_expected,
            has_unordered_materials,
            job_supplier_orders,
            material_readiness_date,
        )

        ready, reason = material_readiness_date(self)
        derived = derived_material_expected(self)
        if derived:
            source = "PURCHASE_LINES"
        elif ready:
            source = "JOB"
        else:
            source = None
        return {
            "expectedDate": ready.isoformat() if ready else None,
            "reason": reason,
            "source": source,
            "limitingLine": derived["limitingLine"] if derived else None,
            "missingLeadTimeSuppliers": (
                derived["missingLeadTimeSuppliers"] if derived else []
            ),
            "lines": derived["lines"] if derived else [],
            "supplierOrders": job_supplier_orders(self),
            "notOrderedYet": has_unordered_materials(self),
        }

    def material_lines_summary(self):
        """The job's material lines (not cancelled, consumables excluded) without
        costs, so every role can see what material the job uses."""
        return [
            {
                "id": p.id,
                "materialName": p.material_name,
                "gradeOrSpec": p.grade_or_spec,
                "sizeSpec": p.size_spec,
                "quantity": float(p.quantity) if p.quantity is not None else None,
                "unit": p.unit,
                "status": p.status,
                "poNumber": p.supplier_order.po_number if p.supplier_order else None,
                "supplierName": p.supplier.name if p.supplier else None,
                "expectedDate": (
                    p.current_expected_date.isoformat() if p.current_expected_date else None
                ),
                "dateReceived": p.date_received.isoformat() if p.date_received else None,
                "plannedMaterialId": p.planned_material_id,
            }
            for p in self.material_purchases or []
            if p.cancelled_at is None
        ]

    @property
    def board_color(self) -> str:
        """Color the job's bars use on the schedule board."""
        return self.schedule_color or fallback_schedule_color(self.id)

    @property
    def job_number(self) -> str:
        year = self.created_at.year if self.created_at else datetime.now(timezone.utc).year
        return f"JO-{year}-{(self.id or '')[:4].upper()}"

    def _material_delay(self):
        if self.material_delayed_at is None:
            return None
        order = self.material_delay_supplier_order
        first = min(
            (op for op in self.operations or [] if op.scheduled_start),
            key=lambda op: op.sequence_no or 0,
            default=None,
        )
        return {
            "originalStart": (
                self.material_delay_original_start.isoformat()
                if self.material_delay_original_start
                else None
            ),
            "currentStart": first.scheduled_start.isoformat() if first else None,
            "kind": self.delay_kind or "MATERIAL",
            "reason": self.material_delay_reason,
            "supplierOrderId": self.material_delay_supplier_order_id,
            "poNumber": order.po_number if order else None,
            "delayedAt": self.material_delayed_at.isoformat(),
        }

    def to_dict(self, include_operations=False, viewer_role=None, include_estimate=True):
        from app.models.operation import OperationStatus
        from app.models.user import UserRole

        ops = list(self.operations or [])
        completed = sum(1 for op in ops if op.status == OperationStatus.COMPLETED)
        next_op = next(
            (op for op in ops if op.status != OperationStatus.COMPLETED),
            None,
        )

        def _num(v):
            if v is None:
                return None
            return float(v) if isinstance(v, Decimal) else float(v)

        role = viewer_role.value if isinstance(viewer_role, UserRole) else viewer_role
        hide_commercial = role == UserRole.PRODUCTION_WORKER.value

        data = {
            "id": self.id,
            "jobNumber": self.job_number,
            "clientName": self.client.name if self.client else None,
            "title": self.title,
            "description": self.description,
            "dueDate": self.due_date.isoformat() if self.due_date else None,
            "clientPoNumber": self.client_po_number,
            "status": self.status.value,
            "priority": self.priority.value if self.priority else JobPriority.MODERATE.value,
            "jobType": self.job_type.value if self.job_type else JobType.FABRICATION.value,
            "partCondition": (
                self.part_condition.value
                if self.part_condition
                else PartCondition.RAW_MATERIAL.value
            ),
            "quantity": _num(self.quantity),
            "unitOfMeasure": self.unit_of_measure,
            "rawMaterials": self.raw_materials or [],
            "materialStatus": (
                self.material_status.value
                if self.material_status
                else MaterialStatus.TO_ORDER.value
            ),
            "materialExpectedDate": (
                self.material_expected_date.isoformat()
                if self.material_expected_date
                else None
            ),
            "materialReceivedDate": (
                self.material_received_date.isoformat()
                if self.material_received_date
                else None
            ),
            "supplierId": self.supplier_id,
            "supplierName": self.supplier.name if self.supplier else None,
            "supplierReference": self.supplier_reference,
            "scheduleColor": self.board_color,
            "deliveredAt": self.delivered_at.isoformat() if self.delivered_at else None,
            "forDeliveryAt": self.for_delivery_at.isoformat() if self.for_delivery_at else None,
            "cancelledAt": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "cancelledById": self.cancelled_by_id,
            "cancelledByName": self.cancelled_by.full_name if self.cancelled_by else None,
            "cancelReason": self.cancel_reason,
            "cancelNote": self.cancel_note,
            "archivedAt": self.archived_at.isoformat() if self.archived_at else None,
            "archivedById": self.archived_by_id,
            "isArchived": self.archived_at is not None,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
            "opsCompleted": completed,
            "opsTotal": len(ops),
            "nextOperation": next_op.operation_name if next_op else None,
            "nextOperationWorkerId": next_op.assigned_worker_id if next_op else None,
            "nextOperationWorkerName": (
                next_op.assigned_worker.full_name
                if next_op and next_op.assigned_worker
                else None
            ),
            "nextOperationCrew": next_op.crew_dicts() if next_op else [],
            "needsReplan": bool(self.needs_replan_reason),
            "needsReplanReason": self.needs_replan_reason,
        }
        from app.services.material_purchase_service import material_wait_fields

        data.update(material_wait_fields(self, for_worker=hide_commercial))
        if self.status == JobOrderStatus.DRAFT:
            data["draftStage"] = draft_stage_label(self)
        if not hide_commercial:
            data["clientId"] = self.client_id
            data["poDate"] = self.po_date.isoformat() if self.po_date else None
            data["createdById"] = self.created_by_id
            data["createdByName"] = self.created_by.full_name if self.created_by else None
            data["createdByRole"] = (
                self.created_by.role.value
                if self.created_by and getattr(self.created_by, "role", None)
                else None
            )
            data["amount"] = _num(self.amount)
            data["materialDelay"] = self._material_delay()
        if include_operations:
            data["operations"] = self._serialize_operations(ops)
            data["materialLines"] = self.material_lines_summary()
            if not hide_commercial:
                inv = self.sales_invoice
                data["salesInvoice"] = inv.to_dict() if inv else None
                data["materialReadiness"] = self._material_readiness()
                data["plannedMaterials"] = self.planned_materials_summary()
        if include_estimate:
            from app.services.completion_estimate_service import risk_state

            state = risk_state(self)
            estimate = state["estimate"]
            data["projectedCompletion"] = state["scheduled"].isoformat() if state["scheduled"] else None
            data["predictedCompletion"] = estimate["predictedFinish"] if estimate else None
            data["scheduleFlag"] = state["flag"]
            data["scheduleFlagBasis"] = state["basis"] if state["flag"] else None
            if include_operations:
                data["completionEstimate"] = estimate
        return data
