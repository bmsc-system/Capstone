"""Office Staff dashboard: the office's own work waiting to be done (objective 4.1).

Job orders still to be planned, materials not yet ordered, supplier deliveries
that are late or due this week, completed jobs without a sales invoice, and
consumables due to be bought again.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.job_order import JobOrder, JobOrderStatus, MaterialStatus
from app.models.sales_invoice import SalesInvoice
from app.models.supplier_order import SupplierOrder, SupplierOrderStatus
from app.services import low_stock_service
from app.services import material_purchase_service as mp_service
from app.services.schedule_calendar import shop_now

DUE_SOON_DAYS = 7
OPEN_JOB = (JobOrderStatus.DRAFT, JobOrderStatus.SCHEDULED, JobOrderStatus.IN_PROGRESS)


def _job(job: JobOrder) -> dict:
    return {
        "id": job.id,
        "jobNumber": job.job_number,
        "title": job.title,
        "clientName": job.client.name if job.client else None,
        "dueDate": job.due_date.isoformat() if job.due_date else None,
        "status": job.status.value,
    }


def _materials_to_order():
    jobs = (
        JobOrder.query.options(joinedload(JobOrder.material_purchases), joinedload(JobOrder.client))
        .filter(
            JobOrder.status.in_(OPEN_JOB),
            JobOrder.archived_at.is_(None),
            JobOrder.material_status == MaterialStatus.TO_ORDER,
        )
        .order_by(JobOrder.due_date.asc())
        .all()
    )
    return [_job(j) for j in jobs if not mp_service.placed_lines(j)]


def _invoices_to_record():
    jobs = (
        JobOrder.query.options(joinedload(JobOrder.client))
        .outerjoin(SalesInvoice, SalesInvoice.job_order_id == JobOrder.id)
        .filter(JobOrder.status == JobOrderStatus.COMPLETED, SalesInvoice.id.is_(None))
        .order_by(JobOrder.due_date.asc())
        .all()
    )
    return [_job(j) for j in jobs]


def _deliveries(today):
    orders = (
        SupplierOrder.query.options(
            joinedload(SupplierOrder.supplier), joinedload(SupplierOrder.lines)
        )
        .filter(
            SupplierOrder.status.in_(
                [SupplierOrderStatus.ISSUED, SupplierOrderStatus.PARTIALLY_RECEIVED]
            ),
            SupplierOrder.expected_delivery_date.isnot(None),
            SupplierOrder.expected_delivery_date <= today + timedelta(days=DUE_SOON_DAYS),
        )
        .order_by(SupplierOrder.expected_delivery_date.asc())
        .all()
    )
    return [
        {
            "id": o.id,
            "poNumber": o.po_number,
            "supplierName": o.supplier.name if o.supplier else None,
            "expectedDeliveryDate": o.expected_delivery_date.isoformat(),
            "daysOverdue": o.days_overdue(today),
        }
        for o in orders
    ]


def office_summary() -> dict:
    now = shop_now()
    today = now.date()
    pending = (
        db.session.query(db.func.count(JobOrder.id))
        .filter(JobOrder.status == JobOrderStatus.DRAFT, JobOrder.archived_at.is_(None))
        .scalar()
    )
    from app.services import consumable_request_service
    from app.services.read_cache import get_or_build

    # The replenishment forecast changes only when purchases do (writes clear the cache).
    restock = get_or_build(
        ("office-restock", today.isoformat()),
        lambda: low_stock_service.restock_due_items(today),
        ttl=60,
    )
    deliveries = _deliveries(today)
    return {
        "pendingJobs": int(pending or 0),
        "materialsToOrder": _materials_to_order(),
        "invoicesToRecord": _invoices_to_record(),
        "lateDeliveries": [d for d in deliveries if d["daysOverdue"] > 0],
        "deliveriesDueSoon": [d for d in deliveries if d["daysOverdue"] <= 0],
        # Items the shop floor reported as running low or out, newest first.
        "reportedLow": [r.to_dict() for r in consumable_request_service.open_requests()],
        "restockDue": [
            {
                "id": r["toolId"],
                "name": r["name"],
                "unit": r["unit"],
                "status": r["status"],
                "nextPurchaseOn": r["nextPurchaseOn"],
                "lastBoughtOn": r["lastBoughtOn"],
                "usualQuantity": r["usualQuantity"],
            }
            for r in restock
        ],
    }
