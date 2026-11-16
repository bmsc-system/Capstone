"""Demo-only consumable SCAN history and restock PO lines for forecast / reorder UI.

Tagged with FC-DEMO so runs are idempotent. Invoked from ``flask seed`` and
``scripts/seed_history.py`` (development only — not production DB config).
"""

from __future__ import annotations

from datetime import date, time, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.job_order import JobOrder
from app.models.material_purchase import MaterialPurchase
from app.models.operation import JobOperation, OperationStatus
from app.models.tool import Tool, ToolCategory
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.user import User, UserRole
from app.services.schedule_calendar import shop_local_to_utc, shop_now

TAG = "FC-DEMO"
EVENT_NOTE = f"{TAG} forecast demo scan"
PO_NAME_PREFIX = f"[{TAG}]"

# Stays out of HIST-SEED restock and this helper → forecast "No data yet".
NO_DATA_TOOL_CODE = "INV-BANDSAW"

# Consumables that get PO lead-time lines (except NO_DATA_TOOL_CODE).
PO_TOOL_CODES = (
    "INV-HANDTAP-M10",
    "INV-HANDTAP-M8",
    "INV-DRILL-06",
    "INV-FLAP-115",
    "INV-ENDMILL-10",
    "INV-OIL-CUT",
    "INV-CUT-115",
)

# Few scan days + PO → Estimated
FEW_SCAN_TOOL_CODES = ("INV-HANDTAP-M8", "INV-OIL-CUT")

# ≥14 working days of scans → Computed; on_hand tuned for reorder status
COMPUTED_PROFILES = {
    "INV-DRILL-06": {"scan_days": 22, "scans_per_day": 3, "on_hand": 4},
    "INV-FLAP-115": {"scan_days": 20, "scans_per_day": 2, "on_hand": 18},
    "INV-ENDMILL-10": {"scan_days": 18, "scans_per_day": 1, "on_hand": 40},
    "INV-CUT-115": {"scan_days": 16, "scans_per_day": 2, "on_hand": 55},
}

FICTIONAL_EMAIL_DOMAIN = "@bmsc.local"


def _working_days_before(end: date, count: int) -> list[date]:
    """Mon–Sat shop days ending on or before ``end`` (inclusive), oldest first."""
    days: list[date] = []
    d = end
    while len(days) < count and (end - d).days < count * 3:
        if d.weekday() < 6:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    return days


def _remap_legacy_units():
    unit_remap = {
        "INV-OIL-CUT": ("bottle", "litre"),
    }
    for code, (new_unit, old_unit) in unit_remap.items():
        tool = Tool.query.filter_by(code=code).first()
        if tool and (tool.unit or "").lower() in (old_unit, "liter", "l"):
            tool.legacy_unit = old_unit
            tool.unit = new_unit
            tool.unit_needs_review = True


def wipe_consumable_forecast_demo() -> tuple[int, int]:
    """Remove FC-DEMO scans and consumable PO lines. Returns (events, purchases)."""
    events = ToolEvent.query.filter(ToolEvent.reason == EVENT_NOTE).all()
    for ev in events:
        db.session.delete(ev)

    purchases = MaterialPurchase.query.filter(
        MaterialPurchase.material_name.like(f"{PO_NAME_PREFIX}%"),
        MaterialPurchase.tool_id.isnot(None),
    ).all()
    pids = [p.id for p in purchases]
    if pids:
        ToolEvent.query.filter(ToolEvent.material_purchase_id.in_(pids)).update(
            {ToolEvent.material_purchase_id: None}, synchronize_session=False
        )
    for p in purchases:
        db.session.delete(p)
    db.session.flush()
    return len(events), len(purchases)


def _load_workers():
    workers = (
        User.query.filter(
            User.role == UserRole.PRODUCTION_WORKER,
            User.active.is_(True),
            User.email.like(f"%{FICTIONAL_EMAIL_DOMAIN}"),
        )
        .order_by(User.email)
        .all()
    )
    if not workers:
        raise RuntimeError("No @bmsc.local production workers; run flask seed first.")
    return workers


def _optional_job_op():
    """Link some scans to a demo job operation when present."""
    job = JobOrder.query.filter_by(client_po_number="PO-ABC-1042").first()
    if not job:
        return None, None
    op = (
        JobOperation.query.filter_by(job_order_id=job.id)
        .filter(JobOperation.status != OperationStatus.COMPLETED)
        .order_by(JobOperation.sequence_no)
        .first()
    )
    if not op:
        return job.id, None
    return job.id, op.id


def _add_po_lines(tool, supplier, today: date, rng_leads: list[tuple[int, int]]):
    """Restock lines with ordered/received dates for measured lead times."""
    lines = []
    cursor = today - timedelta(days=90)
    for idx, (order_offset, receive_extra) in enumerate(rng_leads):
        ordered = cursor + timedelta(days=order_offset)
        if ordered.weekday() == 6:
            ordered += timedelta(days=1)
        received = ordered + timedelta(days=receive_extra)
        if received.weekday() == 6:
            received += timedelta(days=1)
        qty = Decimal(str(20 + idx * 5))
        line = MaterialPurchase(
            tool_id=tool.id,
            material_name=f"{PO_NAME_PREFIX} {tool.name}",
            grade_or_spec=tool.size_spec,
            quantity=qty,
            unit=tool.unit or "pcs",
            unit_cost=Decimal("125.00"),
            supplier_id=supplier.id,
            date_ordered=ordered,
            date_received=received,
        )
        db.session.add(line)
        lines.append(line)
    db.session.flush()
    return lines


def _add_scans(
    tool,
    workers,
    scan_days: list[date],
    scans_per_day: int,
    *,
    job_order_id=None,
    operation_id=None,
    link_fraction: float = 0.0,
):
    for i, day in enumerate(scan_days):
        worker = workers[i % len(workers)]
        for j in range(scans_per_day):
            at = shop_local_to_utc(day, time(8 + (j % 4), 15 * (j % 3)))
            link = (
                job_order_id
                if link_fraction and (i + j) % max(1, int(1 / link_fraction)) == 0
                else None
            )
            db.session.add(
                ToolEvent(
                    tool_id=tool.id,
                    worker_id=worker.id,
                    type=ToolEventType.SCAN,
                    quantity=Decimal("1"),
                    stock_delta=Decimal("-1"),
                    reason=EVENT_NOTE,
                    job_order_id=link,
                    operation_id=operation_id if link else None,
                    created_at=at,
                )
            )


def seed_consumable_forecast_demo(*, replace: bool = False) -> dict:
    """
    Seed SCAN events and consumable PO lines for forecast labels and reorder states.
    Safe to call repeatedly unless ``replace=False`` and data already exists.
    """
    if ToolEvent.query.filter(ToolEvent.reason == EVENT_NOTE).first() and not replace:
        return {"skipped": True, "reason": "already seeded"}

    wipe_consumable_forecast_demo()
    _remap_legacy_units()

    from app.seed.seed_data import _ensure_suppliers

    suppliers = _ensure_suppliers()
    if not suppliers:
        return {"skipped": True, "reason": "no suppliers"}
    supplier = suppliers[0]
    alt_supplier = suppliers[1] if len(suppliers) > 1 else supplier

    workers = _load_workers()
    today = shop_now().date()
    job_id, op_id = _optional_job_op()

    tools_by_code = {
        t.code: t
        for t in Tool.query.filter_by(category=ToolCategory.CONSUMABLE).all()
        if t.code
    }

    po_count = 0
    scan_count = 0

    lead_patterns = {
        "INV-HANDTAP-M10": [(5, 4), (35, 5), (65, 7)],
        "INV-HANDTAP-M8": [(10, 5), (45, 6)],
        "INV-DRILL-06": [(8, 5), (40, 8), (72, 5)],
        "INV-FLAP-115": [(12, 6), (50, 5)],
        "INV-ENDMILL-10": [(15, 7)],
        "INV-OIL-CUT": [(20, 5), (55, 9)],
        "INV-CUT-115": [(25, 6), (60, 5)],
    }

    for code in PO_TOOL_CODES:
        tool = tools_by_code.get(code)
        if not tool:
            continue
        sup = alt_supplier if code in ("INV-OIL-CUT", "INV-HANDTAP-M10") else supplier
        leads = lead_patterns.get(code, [(10, 5)])
        _add_po_lines(tool, sup, today, leads)
        po_count += len(leads)

    for code in FEW_SCAN_TOOL_CODES:
        tool = tools_by_code.get(code)
        if not tool:
            continue
        days = _working_days_before(today - timedelta(days=2), 8)
        _add_scans(tool, workers, days, 1, job_order_id=job_id, operation_id=op_id, link_fraction=0.25)
        scan_count += len(days)

    for code, profile in COMPUTED_PROFILES.items():
        tool = tools_by_code.get(code)
        if not tool:
            continue
        days = _working_days_before(today - timedelta(days=1), profile["scan_days"])
        _add_scans(
            tool,
            workers,
            days,
            profile["scans_per_day"],
            job_order_id=job_id,
            operation_id=op_id,
            link_fraction=0.15,
        )
        scan_count += len(days) * profile["scans_per_day"]
        tool.quantity_on_hand = Decimal(str(profile["on_hand"]))

    db.session.flush()
    return {
        "skipped": False,
        "poLines": po_count,
        "scanEvents": scan_count,
        "noDataTool": NO_DATA_TOOL_CODE,
    }
