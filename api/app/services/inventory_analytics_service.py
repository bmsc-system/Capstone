"""Inventory analytics for the Analytics → Inventory tab (objective 4.1).

Consumables: what was used in the period (QR scans), by month and by item.
Raw materials (bought for each job order, rarely kept in stock): which
materials the jobs use most, how long they take to arrive, how often they came
by the promised date, and which jobs are still waiting for theirs.

Values are quantity × unit cost: the PO line's unit cost for job materials,
the consumable's current unit cost for scans.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.constants.scheduling import SHOP_TIMEZONE
from app.extensions import db
from app.models.material_purchase import MaterialPurchase
from app.models.tool import Tool
from app.models.tool_event import ToolEvent, ToolEventType
from app.services.schedule_calendar import shop_now

TOP_N = 8
WAITING_N = 8


def _f(v, places=2):
    return round(float(v or 0), places)


def _months(period_from: date, period_to: date) -> list[str]:
    out, y, m = [], period_from.year, period_from.month
    while (y, m) <= (period_to.year, period_to.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _value(line: MaterialPurchase) -> Decimal:
    return Decimal(str(line.quantity or 0)) * Decimal(str(line.unit_cost or 0))


def _job_material_lines():
    return MaterialPurchase.query.options(
        joinedload(MaterialPurchase.job_order), joinedload(MaterialPurchase.supplier_order)
    ).filter(
        MaterialPurchase.job_order_id.isnot(None),
        MaterialPurchase.cancelled_at.is_(None),
    )


def _consumable_use(start_utc, end_utc):
    """Scans in the period (undone scans left out), by shop month and by item.
    Value uses the same unit cost as ABC: the latest received PO price, else the
    catalog unit cost."""
    from app.services.forecast_service import _unit_costs_batch

    shop_time = func.timezone(SHOP_TIMEZONE, ToolEvent.created_at)
    month = func.to_char(shop_time, "YYYY-MM")
    rows = (
        db.session.query(ToolEvent.tool_id, month, func.sum(ToolEvent.quantity))
        .filter(
            ToolEvent.type == ToolEventType.SCAN,
            ToolEvent.undone_at.is_(None),
            ToolEvent.tool_id.isnot(None),
            ToolEvent.created_at >= start_utc,
            ToolEvent.created_at < end_utc,
        )
        .group_by(ToolEvent.tool_id, month)
        .all()
    )
    tools = {t.id: t for t in Tool.query.filter(Tool.id.in_({r[0] for r in rows})).all()} if rows else {}
    cost = _unit_costs_batch(list(tools.values()))
    by_month: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    by_item: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for tool_id, m, qty in rows:
        q = float(qty or 0)
        v = q * cost.get(tool_id, 0.0)
        by_month[m][0] += q
        by_month[m][1] += v
        by_item[tool_id][0] += q
        by_item[tool_id][1] += v
    ranked = sorted(by_item.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))
    items = [
        (tid, tools[tid].name, tools[tid].size_spec, tools[tid].unit, q, v)
        for tid, (q, v) in ranked
        if tid in tools
    ]
    total_qty = sum(q for q, _v in by_month.values())
    total_value = sum(v for _q, v in by_month.values())
    return by_month, items, total_qty, total_value


def inventory_summary(from_s=None, to_s=None) -> dict:
    from app.services.analytics_service import _parse_period

    period_from, period_to, start_utc, end_utc = _parse_period(from_s, to_s)
    today = shop_now().date()
    months = _months(period_from, period_to)

    # Consumables used (scans).
    use_by_month, use_by_item, use_qty, use_value = _consumable_use(start_utc, end_utc)

    raw = _raw_materials(period_from, period_to, today)

    return {
        "from": period_from.isoformat(),
        "to": period_to.isoformat(),
        "today": today.isoformat(),
        "months": [
            {
                "month": m,
                "consumableUseQty": _f(use_by_month.get(m, (0, 0))[0], 1),
                "consumableUseValue": _f(use_by_month.get(m, (0, 0))[1]),
            }
            for m in months
        ],
        "consumables": {
            "useQty": _f(use_qty, 1),
            "useValue": _f(use_value),
            "itemsUsed": len(use_by_item),
            "topByValue": [
                {
                    "toolId": tid,
                    "name": name,
                    "sizeSpec": size,
                    "unit": unit,
                    "quantity": _f(q, 1),
                    "value": _f(v),
                }
                for tid, name, size, unit, q, v in use_by_item[:TOP_N]
            ],
        },
        "rawMaterials": raw,
    }


def _material_label(ln: MaterialPurchase) -> str:
    name = " ".join((ln.material_name or "").split())
    # A catalog name that is only a grade number ("5052") reads better with its
    # family in front ("Aluminium 5052").
    catalog = getattr(ln, "material_catalog", None)
    if name.replace(" ", "").isdigit() and catalog is not None and catalog.category:
        name = f"{catalog.category} {name}"
    grade = " ".join((ln.grade_or_spec or "").split())
    return f"{name} · {grade}" if grade else name


def _material_key(ln: MaterialPurchase) -> str:
    """The same material typed in different case or spacing counts once."""
    return _material_label(ln).casefold()


def _raw_materials(period_from: date, period_to: date, today: date) -> dict:
    """Raw materials bought for job orders: most used, delivery time, and the
    jobs still waiting. Draft and cancelled lines are left out."""
    bought = [
        ln
        for ln in _job_material_lines()
        .options(joinedload(MaterialPurchase.supplier))
        .filter(MaterialPurchase.date_ordered.between(period_from, period_to))
        .all()
        if ln.counts_as_ordered
    ]

    # Most used: how many jobs needed each material, and how much of it.
    labels: dict[str, str] = {}
    for ln in bought:
        labels.setdefault(_material_key(ln), _material_label(ln))
    used = defaultdict(lambda: {"jobs": set(), "lines": 0, "qty": defaultdict(float)})
    for ln in bought:
        row = used[_material_key(ln)]
        row["jobs"].add(ln.job_order_id)
        row["lines"] += 1
        row["qty"][ln.unit or "pcs"] += float(ln.quantity or 0)
    most_used = sorted(used.items(), key=lambda kv: (-len(kv[1]["jobs"]), -kv[1]["lines"], kv[0]))

    # Delivery time: days from order to receipt, for lines received in the period.
    arrived = [
        ln
        for ln in _job_material_lines()
        .options(joinedload(MaterialPurchase.supplier))
        .filter(
            MaterialPurchase.date_received.between(period_from, period_to),
            MaterialPurchase.date_ordered.isnot(None),
        )
        .all()
        if ln.counts_as_ordered
    ]
    for ln in arrived:
        labels.setdefault(_material_key(ln), _material_label(ln))
    delivery = defaultdict(lambda: {"days": [], "late": 0})
    all_days, judged, on_time = [], 0, 0
    for ln in arrived:
        days = max((ln.date_received - ln.date_ordered).days, 0)
        all_days.append(days)
        row = delivery[_material_key(ln)]
        row["days"].append(days)
        promised = ln.promised_date
        if promised is not None:
            judged += 1
            if ln.date_received <= promised:
                on_time += 1
            else:
                row["late"] += 1
    slowest = sorted(
        delivery.items(),
        key=lambda kv: (-(sum(kv[1]["days"]) / len(kv[1]["days"])), kv[0]),
    )

    # Jobs still waiting: ordered, not yet received, job not finished.
    open_statuses = ("DRAFT", "SCHEDULED", "IN_PROGRESS")
    waiting = [
        ln
        for ln in _job_material_lines()
        .options(joinedload(MaterialPurchase.supplier))
        .filter(MaterialPurchase.date_ordered.isnot(None), MaterialPurchase.date_received.is_(None))
        .all()
        if ln.counts_as_ordered and ln.job_order is not None and ln.job_order.status.value in open_statuses
    ]
    waiting.sort(key=lambda ln: (-ln.days_overdue(today), ln.current_expected_date or date.max))

    def qty_text(qty: dict) -> str:
        return ", ".join(f"{_f(q, 2):g} {unit}" for unit, q in sorted(qty.items()))

    return {
        "boughtLines": len(bought),
        "boughtJobs": len({ln.job_order_id for ln in bought}),
        "boughtValue": _f(sum((_value(ln) for ln in bought), Decimal("0"))),
        "mostUsed": [
            {
                "material": labels[key],
                "jobs": len(row["jobs"]),
                "lines": row["lines"],
                "quantity": qty_text(row["qty"]),
            }
            for key, row in most_used[:TOP_N]
        ],
        "avgDeliveryDays": round(sum(all_days) / len(all_days), 1) if all_days else None,
        "deliveries": len(all_days),
        "onTimePct": round(on_time / judged * 100) if judged else None,
        "deliveryByMaterial": [
            {
                "material": labels[key],
                "avgDays": round(sum(row["days"]) / len(row["days"]), 1),
                "deliveries": len(row["days"]),
                "late": row["late"],
            }
            for key, row in slowest[:TOP_N]
        ],
        "waitingJobs": len({ln.job_order_id for ln in waiting}),
        "waitingLate": sum(1 for ln in waiting if ln.days_overdue(today) > 0),
        "waiting": [
            {
                "id": ln.id,
                "material": _material_label(ln),
                "sizeSpec": ln.size_spec,
                "quantity": _f(ln.quantity),
                "unit": ln.unit,
                "supplierName": ln.supplier.name if ln.supplier else None,
                "expectedDate": ln.current_expected_date.isoformat() if ln.current_expected_date else None,
                "daysLate": ln.days_overdue(today),
                "jobOrderId": ln.job_order_id,
                "jobNumber": ln.job_order.job_number,
            }
            for ln in waiting[:WAITING_N]
        ],
    }
