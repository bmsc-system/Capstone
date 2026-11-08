"""Material purchase lines on job orders."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.job_order import JobOrder, MaterialStatus
from app.models.material_purchase import MaterialPurchase
from app.models.supplier import Supplier
from app.utils.errors import AppError


def _parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise AppError("Invalid date (YYYY-MM-DD)", "VALIDATION_ERROR", 400) from exc


def _parse_decimal(value, field):
    if value is None or value == "":
        raise AppError(f"{field} is required", "VALIDATION_ERROR", 400)
    try:
        d = Decimal(str(value))
    except Exception as exc:
        raise AppError(f"{field} must be a number", "VALIDATION_ERROR", 400) from exc
    if d < 0:
        raise AppError(f"{field} cannot be negative", "VALIDATION_ERROR", 400)
    return d


def list_purchases_for_job(job_order_id):
    return (
        MaterialPurchase.query.filter_by(job_order_id=job_order_id)
        .order_by(MaterialPurchase.date_ordered.asc(), MaterialPurchase.created_at.asc())
        .all()
    )


def list_purchases(
    *,
    from_s=None,
    to_s=None,
    supplier_id=None,
    material=None,
    status=None,
):
    """
    Cross-job purchase lines for inventory view.
    Date filter applies to date_ordered. status: ORDERED | RECEIVED | CONSUMED
    (RECEIVED = on hand: delivered, job not started yet).
    Summary covers every status so the On order / On hand / Consumed cards stay
    visible while the table is filtered.
    """
    from sqlalchemy.orm import joinedload

    status_s = (status or "").strip().upper()
    if status_s and status_s not in PURCHASE_STATUSES + ("OVERDUE",):
        raise AppError(
            "status must be ORDERED, RECEIVED, CONSUMED or OVERDUE", "VALIDATION_ERROR", 400
        )

    # Job materials only: consumable restock is stock on the consumables list.
    q = MaterialPurchase.query.options(
        joinedload(MaterialPurchase.supplier),
        joinedload(MaterialPurchase.job_order),
        joinedload(MaterialPurchase.supplier_order),
    ).filter(MaterialPurchase.job_order_id.isnot(None))
    d0 = _parse_date(from_s)
    d1 = _parse_date(to_s)
    if d0:
        q = q.filter(MaterialPurchase.date_ordered >= d0)
    if d1:
        q = q.filter(MaterialPurchase.date_ordered <= d1)
    if supplier_id:
        q = q.filter(MaterialPurchase.supplier_id == supplier_id)
    if material:
        q = q.filter(MaterialPurchase.material_name.ilike(f"%{material.strip()}%"))

    # Inventory shows material placed with a supplier: no draft or cancelled lines.
    rows = [
        r
        for r in q.order_by(
            MaterialPurchase.date_ordered.desc(),
            MaterialPurchase.created_at.desc(),
        ).all()
        if r.counts_as_ordered
    ]

    buckets = {s: _empty_bucket() for s in PURCHASE_STATUSES}
    for r in rows:
        _add_to_bucket(buckets[r.status], r)

    if status_s == "OVERDUE":
        items = [r for r in rows if r.days_overdue() > 0]
    else:
        items = [r for r in rows if not status_s or r.status == status_s]
    total_spend = sum((r.line_total for r in rows), Decimal("0"))
    from datetime import date

    from app.models.job_order import JobOrder, MaterialStatus
    from app.services.schedule_calendar import shop_now

    today = shop_now().date()
    month_start = date(today.year, today.month, 1)
    received_this_month = sum(
        1
        for r in rows
        if r.date_received is not None and r.date_received >= month_start
    )
    review_after_cancel = sum(1 for r in rows if r.review_after_job_cancel)
    jobs_waiting = JobOrder.query.filter(
        JobOrder.material_status == MaterialStatus.TO_ORDER,
        JobOrder.cancelled_at.is_(None),
    ).count()
    return {
        "items": [r.to_dict() for r in items],
        "summary": {
            "purchaseCount": len(rows),
            "overdueCount": sum(1 for r in rows if r.days_overdue() > 0),
            "totalSpend": float(total_spend),
            "awaitingDeliveryCount": buckets["ORDERED"]["count"],
            "receivedThisMonthCount": received_this_month,
            "jobsWaitingOnMaterialsCount": jobs_waiting,
            "reviewAfterJobCancelCount": review_after_cancel,
            "onOrder": _bucket_out(buckets["ORDERED"]),
            "onHand": _bucket_out(buckets["RECEIVED"]),
            "consumed": _bucket_out(buckets["CONSUMED"]),
        },
    }


PURCHASE_STATUSES = ("ORDERED", "RECEIVED", "CONSUMED")


def _empty_bucket():
    return {"count": 0, "value": Decimal("0"), "byUnit": {}}


def _add_to_bucket(bucket, row: MaterialPurchase):
    bucket["count"] += 1
    bucket["value"] += row.line_total
    unit = row.unit or "pcs"
    bucket["byUnit"][unit] = bucket["byUnit"].get(unit, Decimal("0")) + Decimal(
        str(row.quantity or 0)
    )


def _bucket_out(bucket):
    return {
        "count": bucket["count"],
        "value": float(bucket["value"]),
        "quantityByUnit": [
            {"unit": u, "quantity": float(q)}
            for u, q in sorted(bucket["byUnit"].items())
        ],
    }


def placed_lines(job: JobOrder) -> list[MaterialPurchase]:
    """Lines placed with a supplier: not cancelled and not on a draft PO."""
    return [p for p in (job.material_purchases or []) if p.counts_as_ordered]


def outstanding_lines(job: JobOrder) -> list[MaterialPurchase]:
    """Placed purchase lines still ORDERED (not delivered)."""
    return [p for p in placed_lines(job) if p.date_received is None]


def line_expected_arrival(p: MaterialPurchase, today: date | None = None) -> dict:
    """A received line arrives on its received date; a PO line on its order's
    expected delivery date; a line recorded without a PO on date ordered plus
    the supplier's stated lead time. Otherwise the arrival is unknown (None).

    A line still awaited after that date is overdue: it is still pending and
    is expected tomorrow at the earliest."""
    from app.services.schedule_calendar import shop_now

    today = today or shop_now().date()
    lead = p.supplier.typical_lead_time_days if p.supplier else None
    order_expected = p.supplier_order.expected_delivery_date if p.supplier_order else None
    days_overdue = 0
    if p.date_received is not None:
        arrival, basis = p.date_received, "RECEIVED"
    elif order_expected is not None:
        arrival, basis = order_expected, "LEAD_TIME"
    elif lead is not None and p.date_ordered is not None:
        arrival, basis = p.date_ordered + timedelta(days=lead), "LEAD_TIME"
    else:
        arrival, basis = None, "UNKNOWN"
    if basis == "LEAD_TIME" and arrival < today:
        days_overdue = (today - arrival).days
        arrival = today + timedelta(days=1)
    return {
        "purchaseId": p.id,
        "materialName": p.material_name,
        "gradeOrSpec": p.grade_or_spec,
        "supplierName": p.supplier.name if p.supplier else None,
        "poNumber": p.supplier_order.po_number if p.supplier_order else None,
        "dateOrdered": p.date_ordered.isoformat() if p.date_ordered else None,
        "leadTimeDays": lead,
        "dateReceived": p.date_received.isoformat() if p.date_received else None,
        "expectedArrival": arrival.isoformat() if arrival else None,
        "basis": basis,
        "fromOrderDeliveryDate": basis == "LEAD_TIME" and order_expected is not None,
        "daysOverdue": days_overdue,
    }


def derived_material_expected(job: JobOrder) -> dict | None:
    """Job's material-ready date from its purchase lines: the latest line arrival.

    None when material is not required or no purchase is recorded yet. When any
    line's arrival is unknown the job's date is unknown too (expectedDate None)
    and ``missingLeadTimeSuppliers`` names the suppliers to fix.
    """
    if job.material_status == MaterialStatus.NOT_REQUIRED:
        return None
    lines = placed_lines(job)
    if not lines:
        return None
    rows = [line_expected_arrival(p) for p in lines]
    unknown = [r for r in rows if r["expectedArrival"] is None]
    missing = sorted({r["supplierName"] or "Unnamed supplier" for r in unknown})
    latest = None if unknown else max(rows, key=lambda r: r["expectedArrival"])
    return {
        "expectedDate": latest["expectedArrival"] if latest else None,
        "allReceived": all(r["basis"] == "RECEIVED" for r in rows),
        "limitingLine": latest,
        "missingLeadTimeSuppliers": missing,
        "lines": rows,
    }


def has_unordered_materials(job: JobOrder) -> bool:
    """True while a To order job has no line on an issued supplier order."""
    if job.material_status == MaterialStatus.NOT_REQUIRED:
        return False
    return not placed_lines(job)


def lead_time_floor(today: date | None = None) -> tuple[date | None, int | None]:
    """(today + longest stated lead time among active suppliers, that lead time).

    (None, None) when no active supplier states a lead time.
    """
    from app.services.schedule_calendar import shop_now

    lead = (
        db.session.query(db.func.max(Supplier.typical_lead_time_days))
        .filter(Supplier.active.is_(True))
        .scalar()
    )
    if lead is None:
        return None, None
    today = today or shop_now().date()
    return today + timedelta(days=int(lead)), int(lead)


def lead_time_floor_reason(floor: date, lead: int) -> str:
    return (
        f"materials not ordered yet — earliest {floor.isoformat()} "
        f"(today + {lead} day longest supplier lead time)"
    )


def scheduling_material_floor(job: JobOrder) -> tuple[date | None, str | None]:
    """Earliest date the first operation may start, as far as materials go.

    The later of the placed lines' arrival and, while anything is still
    unordered, today plus the longest active supplier lead time.
    """
    ready, reason = material_readiness_date(job)
    if not has_unordered_materials(job):
        return ready, reason
    floor, lead = lead_time_floor()
    if floor is None or (ready is not None and ready >= floor):
        return ready, reason
    return floor, lead_time_floor_reason(floor, lead)


def assert_material_date_known(job: JobOrder) -> None:
    """Refuse to schedule while a line's arrival cannot be worked out."""
    derived = derived_material_expected(job)
    if derived and derived["missingLeadTimeSuppliers"]:
        names = ", ".join(derived["missingLeadTimeSuppliers"])
        raise AppError(
            f"Material arrival is unknown: {names} has no lead time. Set the "
            "supplier's lead time, or an expected delivery date on the supplier "
            "order, before proposing a schedule.",
            "MATERIAL_DATE_UNKNOWN",
            400,
        )


def job_supplier_orders(job: JobOrder) -> list[dict]:
    """The job's supplier orders (drafts included, cancelled lines excluded),
    plus one entry per supplier for lines recorded without a PO."""
    groups: dict = {}
    for p in job.material_purchases or []:
        if p.cancelled_at is not None:
            continue
        order = p.supplier_order
        key = ("order", order.id) if order else ("no_po", p.supplier_id)
        g = groups.get(key)
        if g is None:
            supplier = order.supplier if order else p.supplier
            g = groups[key] = {
                "supplierOrderId": order.id if order else None,
                "poNumber": order.po_number if order else None,
                "supplierName": supplier.name if supplier else None,
                "status": order.status.value if order else None,
                "expectedDeliveryDate": (
                    order.expected_delivery_date.isoformat()
                    if order and order.expected_delivery_date
                    else None
                ),
                "lineCount": 0,
                "daysOverdue": 0,
            }
        g["lineCount"] += 1
        g["daysOverdue"] = max(g["daysOverdue"], p.days_overdue())
    return list(groups.values())


def material_readiness_date(job: JobOrder) -> tuple[date | None, str | None]:
    """(date, reason) the scheduler should wait for, or (None, None).

    Worked out from the placed purchase lines only. Returns (None, reason) when
    a line's arrival is unknown.
    """
    if job.material_status == MaterialStatus.NOT_REQUIRED:
        return None, None
    derived = derived_material_expected(job)
    if derived:
        if derived["expectedDate"] is None:
            names = ", ".join(derived["missingLeadTimeSuppliers"])
            return None, f"material arrival unknown — {names} has no lead time"
        d = date.fromisoformat(derived["expectedDate"])
        line = derived["limitingLine"]
        if derived["allReceived"]:
            return d, f"material received {d.isoformat()}"
        name = line["materialName"]
        supplier = line["supplierName"] or "supplier"
        if line["basis"] == "RECEIVED":
            detail = f"{name} received {line['dateReceived']}"
        elif line.get("daysOverdue"):
            days = line["daysOverdue"]
            source = line["poNumber"] or f"ordered {line['dateOrdered']}"
            detail = (
                f"{name} from {supplier}, {source} overdue by {days} day"
                f"{'' if days == 1 else 's'}; expected tomorrow at the earliest"
            )
        elif line["fromOrderDeliveryDate"]:
            detail = f"{name} from {supplier}, {line['poNumber']} expected delivery"
        else:
            detail = (
                f"{name} from {supplier}, ordered {line['dateOrdered']} "
                f"+ {line['leadTimeDays']} day lead time"
            )
        return d, f"material expected {d.isoformat()} — {detail}"
    if job.material_received_date:
        return job.material_received_date, (
            f"material received {job.material_received_date.isoformat()}"
        )
    return None, None


def job_has_started(job: JobOrder) -> bool:
    return any(op.actual_start for op in (job.operations or []))


def material_start_block(job: JobOrder, *, for_worker: bool = False) -> dict | None:
    """Why the job's first operation cannot start yet for materials, or None.

    A job with materials Not required starts freely. A To order job needs at
    least one line on an issued supplier order and every such line received.
    Returns {"code", "message"}.
    """
    if job.material_status == MaterialStatus.NOT_REQUIRED:
        return None

    outstanding = outstanding_lines(job)
    if not outstanding:
        if placed_lines(job):
            return None
        if for_worker:
            message = (
                "The materials for this job have not been ordered yet. "
                "Please check with the office."
            )
        else:
            message = (
                f"No material has been ordered for job {job.job_number} "
                f"({job.title}). Order it and issue the supplier order, or the "
                "Admin can set Materials needed to Not required if the shop already has it."
            )
        return {"code": "MATERIALS_NOT_ORDERED", "message": message}
    if for_worker:
        message = (
            "The materials for this job have not arrived yet. "
            "Please wait for the office to receive them before starting."
        )
    else:
        names = ", ".join(
            f"{p.material_name}{f' ({p.grade_or_spec})' if p.grade_or_spec else ''}"
            for p in outstanding
        )
        message = (
            f"Materials not yet received for this job: {names}. "
            "Receive the supplier order before starting the first operation."
        )
    return {"code": "MATERIALS_NOT_RECEIVED", "message": message}


def material_wait(job: JobOrder, *, for_worker: bool = False) -> dict | None:
    """"Waiting for materials": a released job that has not started and whose
    first operation the start gate would refuse. {"code", "message"} or None."""
    from app.models.job_order import JobOrderStatus

    if job.status in (
        JobOrderStatus.DRAFT,
        JobOrderStatus.COMPLETED,
        JobOrderStatus.FOR_DELIVERY,
        JobOrderStatus.DELIVERED,
    ):
        return None
    if job_has_started(job):
        return None
    return material_start_block(job, for_worker=for_worker)


def material_wait_fields(job: JobOrder, *, for_worker: bool = False) -> dict:
    wait = material_wait(job, for_worker=for_worker)
    return {
        "waitingForMaterials": wait is not None,
        "materialWaitCode": wait["code"] if wait else None,
        "materialWaitReason": wait["message"] if wait else None,
    }


def consume_received_lines(job: JobOrder, when) -> int:
    """Mark every RECEIVED line consumed (job's first operation started)."""
    n = 0
    for p in job.material_purchases or []:
        if p.date_received is not None and p.consumed_at is None:
            p.consumed_at = when
            n += 1
    return n


def _consume_if_job_started(purchase: MaterialPurchase):
    """A line delivered after the job already started goes straight to CONSUMED."""
    from datetime import datetime, timezone

    if (
        purchase.date_received is not None
        and purchase.consumed_at is None
        and purchase.job_order is not None
        and job_has_started(purchase.job_order)
    ):
        purchase.consumed_at = datetime.now(timezone.utc)


def sync_job_material_from_purchases(job: JobOrder):
    """
    Derive job material_status from purchase lines.
    - Any purchases → at least ORDERED (unless already RECEIVED / NOT_REQUIRED)
    - All lines have date_received → RECEIVED + material_received_date = max(received)
    - No placed lines left (all cancelled / still on a draft PO) → back to TO_ORDER
    Only placed lines count (see ``placed_lines``).
    """
    if job.material_status == MaterialStatus.NOT_REQUIRED:
        return

    lines = placed_lines(job)
    if not lines:
        if job.material_status in (MaterialStatus.ORDERED, MaterialStatus.RECEIVED):
            job.material_status = MaterialStatus.TO_ORDER
            job.material_received_date = None
        return

    all_received = all(p.date_received is not None for p in lines)
    if all_received:
        job.material_status = MaterialStatus.RECEIVED
        job.material_received_date = max(p.date_received for p in lines)
        return

    if job.material_status in (MaterialStatus.TO_ORDER, MaterialStatus.RECEIVED):
        # Partially received or newly ordered: stay / move to ORDERED
        job.material_status = MaterialStatus.ORDERED
        job.material_received_date = None


PICK_MATERIAL_MESSAGE = "Pick a material from the catalog."


def require_active_catalog_item(catalog_id):
    """The active catalog material a new job-material PO line must reference."""
    from app.models.material_catalog import MaterialCatalogItem

    item = db.session.get(MaterialCatalogItem, catalog_id) if catalog_id else None
    if item is None or not item.active:
        raise AppError(PICK_MATERIAL_MESSAGE, "VALIDATION_ERROR", 400)
    return item


def check_material_unit(unit: str, keep: str | None = None):
    """``keep``: the unit already saved on the line, accepted even if no longer offered."""
    from app.models.material_catalog import MATERIAL_UNITS

    if unit not in MATERIAL_UNITS and unit != keep:
        raise AppError(
            f"Unit must be one of {', '.join(MATERIAL_UNITS)}", "VALIDATION_ERROR", 400
        )


def build_draft_line(job: JobOrder, supplier: Supplier, data: dict) -> MaterialPurchase:
    """Validate one job material line for a draft supplier order (not added to
    the session). The material is picked from the catalog (``materialCatalogId``,
    active items only); the job must need materials (To order)."""
    if job.material_status == MaterialStatus.NOT_REQUIRED:
        raise AppError(
            f"{job.job_number} has Materials needed set to Not required. Set it to "
            "To order first if materials must be bought for it.",
            "MATERIALS_NOT_REQUIRED",
            409,
        )
    from app.services.planned_materials import require_planned_line

    planned = require_planned_line(job, data.get("plannedMaterialId"))
    item = require_active_catalog_item(
        data.get("materialCatalogId") or data.get("material_catalog_id")
    )
    if planned is not None and planned.get("materialCatalogId") not in (None, item.id):
        raise AppError(
            f"This line is for the planned {planned.get('name')}; pick that material.",
            "VALIDATION_ERROR",
            400,
        )

    qty = _parse_decimal(data.get("quantity"), "quantity")
    if qty <= 0:
        raise AppError("quantity must be greater than zero", "VALIDATION_ERROR", 400)
    unit_cost = _parse_decimal(
        data.get("unitCost") if data.get("unitCost") is not None else data.get("unit_cost", 0),
        "unitCost",
    )
    unit = (data.get("unit") or "").strip() or item.default_unit or "pcs"
    check_material_unit(unit, keep=(planned or {}).get("unit"))
    grade = (
        data.get("gradeOrSpec") or data.get("grade_or_spec") or ""
    ).strip() or None
    size_spec = (data.get("sizeSpec") or data.get("size_spec") or "").strip() or None
    if size_spec and len(size_spec) > 255:
        raise AppError("sizeSpec is too long", "VALIDATION_ERROR", 400)

    return MaterialPurchase(
        job_order=job,
        material_catalog_id=item.id,
        # Text snapshot; kept as typed on the PO even if the catalog name changes later.
        material_name=item.name,
        grade_or_spec=grade,
        size_spec=size_spec,
        quantity=qty,
        unit=unit,
        unit_cost=unit_cost,
        supplier_id=supplier.id,
        planned_material_id=planned.get("id") if planned is not None else None,
    )


def create_purchase(job: JobOrder, data: dict, actor_id: str) -> MaterialPurchase:
    """A job's purchase line goes onto the supplier's open draft order."""
    from app.services import supplier_order_service as so_service

    supplier_id = data.get("supplierId") or data.get("supplier_id")
    _order, lines = so_service.add_lines_to_draft(
        supplier_id, [{**data, "jobOrderId": job.id}], actor_id
    )
    return lines[0]


def _refuse_po_line_edit(purchase: MaterialPurchase):
    if purchase.supplier_order is not None:
        raise AppError(
            "This line belongs to a supplier order. Change it on the supplier order.",
            "ON_SUPPLIER_ORDER",
            409,
        )


def update_purchase(purchase: MaterialPurchase, data: dict) -> MaterialPurchase:
    """Edit a line recorded without a PO."""
    _refuse_po_line_edit(purchase)
    job = purchase.job_order
    if "materialName" in data or "material_name" in data:
        name = (data.get("materialName") or data.get("material_name") or "").strip()
        if not name:
            raise AppError("materialName is required", "VALIDATION_ERROR", 400)
        purchase.material_name = name
    if "gradeOrSpec" in data or "grade_or_spec" in data:
        purchase.grade_or_spec = (
            data.get("gradeOrSpec") or data.get("grade_or_spec") or ""
        ).strip() or None
    if "quantity" in data:
        qty = _parse_decimal(data.get("quantity"), "quantity")
        if qty <= 0:
            raise AppError("quantity must be greater than zero", "VALIDATION_ERROR", 400)
        purchase.quantity = qty
    if "unit" in data:
        purchase.unit = (data.get("unit") or "pcs").strip() or "pcs"
    if "unitCost" in data or "unit_cost" in data:
        purchase.unit_cost = _parse_decimal(
            data.get("unitCost", data.get("unit_cost")), "unitCost"
        )
    if "supplierId" in data or "supplier_id" in data:
        sid = data.get("supplierId") or data.get("supplier_id")
        supplier = Supplier.query.get(sid)
        if not supplier:
            raise AppError("Supplier not found", "NOT_FOUND", 404)
        purchase.supplier_id = supplier.id
    if "dateOrdered" in data or "date_ordered" in data:
        d = _parse_date(data.get("dateOrdered") or data.get("date_ordered"))
        if not d:
            raise AppError("dateOrdered is required", "VALIDATION_ERROR", 400)
        purchase.date_ordered = d
    if "dateReceived" in data or "date_received" in data:
        new_received = _parse_date(
            data.get("dateReceived") if "dateReceived" in data else data.get("date_received")
        )
        if new_received is None and purchase.consumed_at is not None:
            raise AppError(
                "This material was already consumed when the job started; "
                "its received date cannot be cleared.",
                "VALIDATION_ERROR",
                400,
            )
        purchase.date_received = new_received
    if purchase.date_received and purchase.date_ordered:
        if purchase.date_received < purchase.date_ordered:
            raise AppError(
                "dateReceived cannot be before dateOrdered", "VALIDATION_ERROR", 400
            )
    _consume_if_job_started(purchase)
    sync_job_material_from_purchases(job)
    db.session.commit()
    return purchase


def _check_receive_date(purchase: MaterialPurchase, when: date):
    if purchase.date_ordered is not None and when < purchase.date_ordered:
        raise AppError(
            f"Date received cannot be before the order date of "
            f"{purchase.material_name} ({purchase.date_ordered.isoformat()})",
            "VALIDATION_ERROR",
            400,
        )


def _receive_consumable(p: MaterialPurchase, when: date, actor_id: str):
    """A consumable line adds its quantity to stock, like Receive delivery,
    with the RECEIVE event linked to the line (and so to its PO)."""
    from app.models.tool_event import ToolEvent, ToolEventType

    from app.models.tool import Tool
    from app.utils.row_locks import lock_row

    tool = lock_row(Tool, p.tool_id)
    qty = Decimal(str(p.quantity))
    tool.quantity_on_hand = Decimal(str(tool.quantity_on_hand or 0)) + qty
    # Prefer latest received PO unit price for ABC; tools.unit_cost is fallback only.
    if p.unit_cost is not None:
        tool.unit_cost = p.unit_cost
    db.session.add(
        ToolEvent(
            tool_id=tool.id,
            worker_id=actor_id,
            type=ToolEventType.RECEIVE,
            quantity=qty,
            stock_delta=qty,
            reason=p.supplier_order.po_number if p.supplier_order else None,
            supplier=p.supplier.name if p.supplier else None,
            received_on=when,
            material_purchase_id=p.id,
        )
    )
    from app.services.consumable_request_service import close_on_purchase

    close_on_purchase(tool.id, actor_id, material_purchase_id=p.id)


def receive_lines(
    lines: list[MaterialPurchase], received_date=None, *, actor_id=None
) -> list[MaterialPurchase]:
    """Receive whole lines on one date (called by Receive order): updates each
    supplier order's status, each job's material status, and the stock of
    consumable lines (once: a received line cannot be received again)."""
    from app.services.supplier_order_service import recompute_order_status

    from app.services.schedule_calendar import shop_now

    when = _parse_date(received_date) or shop_now().date()
    if any(p.is_consumable for p in lines) and not actor_id:
        raise AppError("Receiving consumables needs the receiver", "VALIDATION_ERROR", 400)
    for p in lines:
        if p.cancelled_at is not None:
            raise AppError(
                f"{p.material_name} was cancelled and cannot be received.",
                "LINE_CANCELLED",
                409,
            )
        if p.is_draft:
            raise AppError(
                f"{p.material_name} is on a draft supplier order. Issue the order first.",
                "ORDER_NOT_ISSUED",
                409,
            )
        if p.date_received is not None:
            raise AppError(
                f"{p.material_name} was already received.", "ALREADY_RECEIVED", 409
            )
        _check_receive_date(p, when)
    if len({p.id for p in lines}) != len(lines):
        raise AppError("A line is listed twice.", "VALIDATION_ERROR", 400)
    for p in lines:
        p.date_received = when
        if p.is_consumable:
            _receive_consumable(p, when, actor_id)
        _consume_if_job_started(p)
    for order in {p.supplier_order for p in lines if p.supplier_order is not None}:
        recompute_order_status(order)
    for job in {p.job_order for p in lines if p.job_order is not None}:
        sync_job_material_from_purchases(job)
    db.session.commit()

    from app.services import material_delay_service

    jobs_by_order = {}
    for p in lines:
        if p.job_order is not None:
            jobs_by_order.setdefault(p.supplier_order, set()).add(p.job_order)
    for order, jobs in jobs_by_order.items():
        material_delay_service.reschedule_jobs(jobs, material_delay_service.RECEIVED, order)
    return lines


def delete_purchase(purchase: MaterialPurchase):
    """Delete a line recorded without a PO (PO lines are removed or cancelled
    on their supplier order)."""
    _refuse_po_line_edit(purchase)
    job = purchase.job_order
    db.session.delete(purchase)
    db.session.flush()
    # Refresh relationship
    db.session.refresh(job)
    sync_job_material_from_purchases(job)
    db.session.commit()
