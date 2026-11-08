"""Local sample transaction history.

``generate`` adds job orders, purchasing, shop-floor time, attendance and
inventory movements that reference the shop's existing people, machines,
clients, suppliers and catalog. It does not create or edit those master rows.

The only existing values it changes are consumable ``quantity_on_hand``.
Each of those is snapshotted in ``sample_records.detail`` and restored by
``remove``. Holiday rows it inserts are tracked the same way and deleted
by ``remove``. Tool-unit custody is left as it was: borrow and return are
new events only.

Dry run builds one transaction, returns the summary, and rolls it back.
"""

from __future__ import annotations

import math
import random
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import event, func
from app.extensions import db
from app.models.attendance import AttendanceRecord
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.consumable_purchase import ConsumablePurchase
from app.models.job_order import (
    JobOrder,
    JobOrderStatus,
    JobPriority,
    JobType,
    MaterialStatus,
    PartCondition,
)
from app.models.machine import MachineType, MachineUnit
from app.models.material_catalog import MaterialCatalogItem
from app.models.material_purchase import MaterialPurchase
from app.models.notification import NotificationLog
from app.models.operation import JobOperation, OperationHelper, OperationStatus, ReworkReasonCategory
from app.models.operation_time import (
    DowntimeCategory,
    MachineDowntime,
    OperationPauseReason,
    OperationTimeEvent,
    OperationTimeLog,
)
from app.models.sales_invoice import SalesInvoice
from app.models.sample_record import SampleRecord
from app.models.schedule_move import DelayKind, MaterialCause, ScheduleMove
from app.models.shop_settings import ShopSettings
from app.models.staff_alert import StaffAlert
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder, SupplierOrderStatus, format_po_number
from app.models.tool import Tool, ToolCategory
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.tool_type import ToolUnit, ToolUnitStatus
from app.models.user import User, UserRole, UserStatus
from app.models.worker_skill import (
    CalendarExceptionType,
    OperationType,
    WorkCalendarException,
    WorkerSchedule,
    WorkerSkill,
)
from app.services.audit_service import (
    _COLUMN_SNAPSHOT_MODELS,
    _after_delete,
    _after_insert,
    _before_update,
)
from app.services.schedule_calendar import (
    build_worker_working_windows,
    clear_calendar_cache,
    default_shop_schedule_by_dow,
    effective_hours_for_date,
    effective_windows_for_date,
    ensure_utc,
    intersect_intervals,
    load_calendar_exceptions,
    load_worker_schedule_maps_many,
    place_unbroken,
    shop_local_to_utc,
    shop_working_hours,
    utc_to_shop,
)

HOLIDAYS = (
    (date(2025, 11, 1), "All Saints' Day"),
    (date(2025, 12, 8), "Feast of the Immaculate Conception"),
    (date(2025, 12, 24), "Christmas Eve"),
    (date(2025, 12, 25), "Christmas Day"),
    (date(2025, 12, 30), "Rizal Day"),
    (date(2025, 12, 31), "Last Day of the Year"),
    (date(2026, 1, 1), "New Year's Day"),
    (date(2026, 2, 17), "Chinese New Year"),
    (date(2026, 4, 2), "Maundy Thursday"),
    (date(2026, 4, 3), "Good Friday"),
    (date(2026, 4, 9), "Araw ng Kagitingan"),
    (date(2026, 5, 1), "Labor Day"),
    (date(2026, 6, 12), "Independence Day"),
    (date(2026, 8, 21), "Ninoy Aquino Day"),
    (date(2026, 8, 31), "National Heroes Day"),
)

# Late share and extra calendar days, tuned so the reliability score lands
# near A / B / C when this history is most of the supplier's deliveries.
SUPPLIER_PROFILES = (
    {"late": 0.04, "extra": 1},
    {"late": 0.18, "extra": 2},
    {"late": 0.26, "extra": 2},
)

PARTS = (
    ("Drive shaft", "Turned and keyed shaft for a gearbox."),
    ("Bronze bushing", "Bored and faced bushing to the drawing."),
    ("Coupling hub", "Bored coupling hub with a keyseat."),
    ("Flange", "Faced flange, bolt circle drilled."),
    ("Mounting bracket", "Milled bracket with slotted holes."),
    ("Spur gear", "Cut spur gear, bore to fit."),
    ("Conveyor roller", "Turned roller with pressed-in ends."),
    ("Pin", "Hard-turned pin, ends chamfered."),
    ("Spacer", "Parted and faced spacer."),
    ("Pulley", "Turned pulley, groove to width."),
    ("Housing cover", "Milled cover, holes tapped."),
    ("Wear plate", "Ground wear plate to thickness."),
    ("Sleeve", "Bored sleeve for a running fit."),
    ("Adapter plate", "Drilled adapter between two flanges."),
    ("Keystock", "Milled key to width and height."),
    ("Collar", "Faced collar with a set-screw hole."),
    ("Guide rail", "Milled guide rail, ends squared."),
    ("Cam", "Milled cam profile."),
    ("Hub", "Turned hub, bore and face."),
    ("Base plate", "Milled base, holes drilled and tapped."),
)

# Pause time is wall-clock and stays on the same shift. Machine downtime is
# planted as its own records so it can carry shop hours without stretching
# a job across lunch. Waiting-for-material pauses are omitted: supplier
# lateness is recorded as a short material move instead.
PAUSE_TARGETS = (
    (OperationPauseReason.OTHER, 0.32, 1.0, "Waiting for instruction"),
    (OperationPauseReason.WAITING_PRIOR_OPERATION, 0.20, 1.0, "Waiting on the prior operation"),
)

REWORK_WEIGHTS = (
    (ReworkReasonCategory.DIMENSION_OUT_OF_TOLERANCE, 0.88, "Dimension out of tolerance"),
    (ReworkReasonCategory.SURFACE_FINISH, 0.05, "Surface finish below the drawing"),
    (ReworkReasonCategory.OPERATOR_ERROR, 0.04, "Setup error, recut required"),
    (ReworkReasonCategory.MACHINE_FAULT, 0.02, "Machine fault marked the part"),
    (ReworkReasonCategory.OTHER, 0.01, "Fit-up did not match the mating part"),
)

DOWNTIME_REASONS = (
    (DowntimeCategory.MECHANICAL_FAILURE, "Spindle drive stopped"),
    (DowntimeCategory.ELECTRICAL_FAULT, "Axis drive fault"),
    (DowntimeCategory.WAITING_FOR_PARTS, "Waiting on a replacement belt"),
    (DowntimeCategory.MECHANICAL_FAILURE, "Coolant pump failed"),
)

# The newest jobs are still open at the end date, as in a shop that takes
# 30-40 jobs a month with one to three weeks to deliver: a few waiting to be
# planned, some waiting on a supplier, and the rest scheduled or in progress.
OPEN_FATES = (
    "draft",
    "draft",
    "waiting",
    "scheduled",
    "waiting",
    "draft",
    "scheduled",
    "scheduled",
    "progress",
    "scheduled",
    "scheduled",
    "progress",
    "scheduled",
    "scheduled",
    "progress",
    "scheduled",
)

_AUDIT_MODELS = (
    JobOrder,
    JobOperation,
    User,
    Tool,
    OperationTimeLog,
    MachineDowntime,
    Client,
    NotificationLog,
    WorkCalendarException,
    *_COLUMN_SNAPSHOT_MODELS,
)

_ONUPDATE_COLUMNS = (
    JobOrder.__table__.c.updated_at,
    MaterialPurchase.__table__.c.updated_at,
    SupplierOrder.__table__.c.updated_at,
    AttendanceRecord.__table__.c.updated_at,
)


def assert_local_database():
    """Refuse anything that is not a database on this machine."""
    url = db.engine.url
    host = (url.host or "").lower()
    raw = str(url).lower()
    if host not in ("localhost", "127.0.0.1") or "neon" in host or "neon.tech" in raw:
        raise ValueError(
            "sample-history only runs on a local database (localhost). "
            f"Refusing host {host!r}."
        )


# Every table that holds shop activity rather than setup. Each foreign key
# that points at one of these comes from another table in this list, so a
# TRUNCATE ... CASCADE never reaches users, machines, clients, suppliers,
# the catalog, tools or settings.
TRANSACTION_TABLES = (
    "job_orders",
    "operations",
    "operation_helpers",
    "operation_time_logs",
    "schedule_moves",
    "sales_invoices",
    "material_purchases",
    "consumable_purchases",
    "supplier_orders",
    "notification_logs",
    "staff_alerts",
    "attendance_records",
    "machine_downtimes",
    "tool_events",
    "stocktakes",
    "stocktake_lines",
    "offline_action_receipts",
    "sample_records",
)


def _wipe_transactions():
    """Delete all shop activity and put borrowed tools back on the rack.

    Users, machines, clients, suppliers, the catalog, tools and consumables,
    the work calendar and settings stay. Machine units whose usual operator
    is not a production worker (an Admin, from before Admins stopped
    working jobs) are opened to all.
    """
    from sqlalchemy import text

    counts = {}
    # From an earlier run of the generator: put back the consumable stock it
    # changed and drop the audit rows it wrote (the rest goes in the TRUNCATE).
    restored = 0
    sample_audit = []
    for row in SampleRecord.query.order_by(SampleRecord.id).all():
        detail = row.detail or {}
        if detail.get("role") == "stock_snapshot":
            tool = db.session.get(Tool, row.record_id)
            if tool is not None:
                tool.quantity_on_hand = Decimal(str(detail.get("quantityOnHand")))
                restored += 1
        elif row.table_name == "audit_logs":
            sample_audit.append(row.record_id)
    counts["sample audit rows"] = _delete_ids("audit_logs", sample_audit) if sample_audit else 0
    counts["stock restored"] = restored
    db.session.flush()
    for table in TRANSACTION_TABLES:
        counts[table] = db.session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
    db.session.execute(text("TRUNCATE " + ", ".join(TRANSACTION_TABLES) + " CASCADE"))
    counts["tool units back on the rack"] = db.session.execute(
        text(
            "UPDATE tool_units SET status = 'AVAILABLE', current_holder_id = NULL, held_since = NULL "
            "WHERE status = 'OUT'"
        )
    ).rowcount
    counts["machine operators cleared"] = db.session.execute(
        text(
            "UPDATE machine_units SET default_operator_id = NULL "
            "WHERE default_operator_id IS NOT NULL AND default_operator_id NOT IN "
            "(SELECT id FROM users WHERE role = 'PRODUCTION_WORKER' AND status <> 'DISABLED')"
        )
    ).rowcount
    db.session.flush()
    return counts


def reset_demo_data(start, end, seed=42, apply=False):
    """Replace all shop activity with fresh sample history (``start``..``end``).

    One transaction: the wipe and the new history commit together or not at
    all. Without ``apply`` everything is rolled back after the summary.
    Runs on any database; the CLI asks for the host name before it commits.
    """
    start_d = _parse_day(start, "start")
    end_d = _parse_day(end, "end")
    if end_d < start_d:
        raise ValueError("end is before start")
    frozen = _freeze_onupdate()
    try:
        with _quiet_audit():
            wiped = _wipe_transactions()
            summary = _generate(start_d, end_d, int(seed))
            summary["wiped"] = wiped
            if apply:
                db.session.commit()
            else:
                db.session.rollback()
            summary["applied"] = bool(apply)
            return summary
    except Exception:
        db.session.rollback()
        raise
    finally:
        _thaw_onupdate(frozen)


def generate(start, end, seed=42, apply=False):
    """Build sample history from ``start`` through ``end`` (inclusive).

    ``apply`` commits. Without it the transaction is rolled back after the
    summary is built. A second generate is refused while a ledger exists.
    """
    assert_local_database()
    start_d = _parse_day(start, "start")
    end_d = _parse_day(end, "end")
    if end_d < start_d:
        raise ValueError("end is before start")
    if db.session.query(SampleRecord.id).first() is not None:
        raise ValueError(
            "Sample history already exists. Run flask sample-history remove first."
        )

    frozen = _freeze_onupdate()
    try:
        with _quiet_audit():
            summary = _generate(start_d, end_d, int(seed))
            if apply:
                db.session.commit()
            else:
                db.session.rollback()
            summary["applied"] = bool(apply)
            return summary
    except Exception:
        db.session.rollback()
        raise
    finally:
        _thaw_onupdate(frozen)


def remove(apply=False):
    """Delete rows the generator created and restore snapshotted stock.

    Without ``apply`` the deletes run and then roll back, so the summary
    shows what would be removed.
    """
    assert_local_database()
    frozen = _freeze_onupdate()
    try:
        with _quiet_audit():
            summary = _remove()
            if apply:
                db.session.commit()
            else:
                db.session.rollback()
            summary["applied"] = bool(apply)
            return summary
    except Exception:
        db.session.rollback()
        raise
    finally:
        _thaw_onupdate(frozen)


_MANUAL_SCRAP_PO = "12738"
_MANUAL_SCRAP_TITLES = frozenset({"title", "frfr"})
_RESTOCK_NOTE = "consumable restock"


def clear_old_demo(apply=False):
    """Delete the pre-generator demo transactions and rewind the stock they received.

    The set is the HIST-SEED jobs, the TC-SEED jobs, the two manual scraps on
    client PO 12738, the purchase orders that belong only to those jobs, and
    the old consumable-restock orders with their receives and the orphan
    adjust. Rows tracked in sample_records are left alone. Master data and
    attendance are not changed, except consumable quantity_on_hand, which
    moves by the deleted stock deltas and never below zero.

    Without ``apply`` the work runs and then rolls back.
    """
    assert_local_database()
    frozen = _freeze_onupdate()
    try:
        with _quiet_audit():
            before = _master_fingerprint()
            attendance_before = AttendanceRecord.query.count()
            summary = _clear_old_demo()
            if _master_fingerprint() != before:
                raise RuntimeError(
                    "clear-old-demo changed a master row other than consumable "
                    "quantity on hand. Rolled back."
                )
            if AttendanceRecord.query.count() != attendance_before:
                raise RuntimeError("clear-old-demo changed attendance. Rolled back.")
            if apply:
                db.session.commit()
            else:
                db.session.rollback()
            summary["applied"] = bool(apply)
            return summary
    except Exception:
        db.session.rollback()
        raise
    finally:
        _thaw_onupdate(frozen)


def format_clear_summary(summary):
    verb = "Deleted" if summary.get("applied") else "Would delete"
    lines = [f"{verb}:"]
    for key, count in summary["counts"].items():
        lines.append(f"  {key}: {count}")
    lines.append("Consumable quantity on hand:")
    if not summary["stock"]:
        lines.append("  no item changed")
    for row in summary["stock"]:
        lines.append(f"  {row['code']} {row['name']}: {row['before']} -> {row['after']}")
    return lines


def _ledger_ids(table):
    return {
        row.record_id
        for row in SampleRecord.query.filter_by(table_name=table).all()
    }


def _clear_old_demo():
    ledger_jobs = _ledger_ids("job_orders")
    jobs = []
    for job in JobOrder.query.order_by(JobOrder.created_at, JobOrder.id).all():
        if job.id in ledger_jobs:
            continue
        po = job.client_po_number or ""
        title = job.title or ""
        if po.startswith("HIST-SEED") or po.startswith("TC-SEED"):
            jobs.append(job)
        elif po == _MANUAL_SCRAP_PO and title in _MANUAL_SCRAP_TITLES:
            jobs.append(job)
    job_ids = [job.id for job in jobs]
    job_id_set = set(job_ids)
    hist = sum(1 for job in jobs if (job.client_po_number or "").startswith("HIST-SEED"))
    tc = sum(1 for job in jobs if (job.client_po_number or "").startswith("TC-SEED"))
    manual = len(jobs) - hist - tc

    op_ids = [
        row.id
        for row in JobOperation.query.filter(JobOperation.job_order_id.in_(job_ids)).all()
    ] if job_ids else []

    ledger_orders = _ledger_ids("supplier_orders")
    job_lines = (
        MaterialPurchase.query.filter(MaterialPurchase.job_order_id.in_(job_ids)).all()
        if job_ids
        else []
    )
    exclusive_order_ids = []
    seen_orders = {line.supplier_order_id for line in job_lines if line.supplier_order_id}
    for order_id in seen_orders:
        if order_id in ledger_orders:
            continue
        siblings = MaterialPurchase.query.filter_by(supplier_order_id=order_id).all()
        if siblings and all(line.job_order_id in job_id_set for line in siblings):
            exclusive_order_ids.append(order_id)

    restock_orders = [
        order
        for order in SupplierOrder.query.filter(SupplierOrder.notes == _RESTOCK_NOTE).all()
        if order.id not in ledger_orders and order.id not in set(exclusive_order_ids)
    ]
    restock_ids = [order.id for order in restock_orders]
    restock_lines = (
        MaterialPurchase.query.filter(MaterialPurchase.supplier_order_id.in_(restock_ids)).all()
        if restock_ids
        else []
    )
    restock_line_ids = [line.id for line in restock_lines]
    ledger_events = _ledger_ids("tool_events")
    receive_events = (
        ToolEvent.query.filter(
            ToolEvent.material_purchase_id.in_(restock_line_ids),
            ToolEvent.type == ToolEventType.RECEIVE,
        ).all()
        if restock_line_ids
        else []
    )
    receive_events = [event for event in receive_events if event.id not in ledger_events]
    adjusts = [
        event
        for event in ToolEvent.query.filter(
            ToolEvent.type == ToolEventType.ADJUST,
            ToolEvent.material_purchase_id.is_(None),
        ).all()
        if event.id not in ledger_events
    ]
    job_events = []
    if job_ids or op_ids:
        job_events = [
            event
            for event in ToolEvent.query.filter(
                db.or_(
                    ToolEvent.job_order_id.in_(job_ids or [""]),
                    ToolEvent.operation_id.in_(op_ids or [""]),
                )
            ).all()
            if event.id not in ledger_events
        ]

    stock_events = {event.id: event for event in receive_events}
    for event in adjusts:
        stock_events[event.id] = event
    delta_by_tool = defaultdict(lambda: Decimal("0"))
    for event in stock_events.values():
        tool_id = event.tool_id
        delta = _signed_stock_delta(event)
        if not tool_id or delta == 0:
            continue
        delta_by_tool[tool_id] += delta

    stock = []
    for tool_id, delta in delta_by_tool.items():
        tool = db.session.get(Tool, tool_id)
        if tool is None or delta == 0:
            continue
        before_qty = Decimal(str(tool.quantity_on_hand or 0))
        after_qty = before_qty - delta
        if after_qty < 0:
            after_qty = Decimal("0")
        tool.quantity_on_hand = after_qty
        stock.append(
            {
                "code": tool.code or "",
                "name": tool.name,
                "before": f"{before_qty:.2f}",
                "after": f"{after_qty:.2f}",
            }
        )
    stock.sort(key=lambda row: (row["code"], row["name"]))

    order_ids = list(dict.fromkeys(exclusive_order_ids + restock_ids))
    purchase_ids = list(dict.fromkeys([line.id for line in job_lines] + restock_line_ids))
    event_ids = list(dict.fromkeys(
        [event.id for event in receive_events]
        + [event.id for event in adjusts]
        + [event.id for event in job_events]
    ))

    counts = {
        "job orders": len(job_ids),
        "HIST-SEED jobs": hist,
        "TC-SEED jobs": tc,
        "manual scraps": manual,
        "operations": len(op_ids),
        "operation helpers": _delete_matching(OperationHelper, OperationHelper.operation_id, op_ids),
        "time logs": _delete_matching(OperationTimeLog, OperationTimeLog.operation_id, op_ids),
        "schedule moves": _delete_or(
            ScheduleMove,
            ScheduleMove.job_order_id, job_ids,
            ScheduleMove.supplier_order_id, order_ids,
        ),
        "machine downtimes": _delete_or(
            MachineDowntime,
            MachineDowntime.job_order_id, job_ids,
            MachineDowntime.operation_id, op_ids,
        ),
        "notification logs": _delete_matching(NotificationLog, NotificationLog.job_order_id, job_ids),
        "staff alerts": _delete_or(
            StaffAlert,
            StaffAlert.job_order_id, job_ids,
            StaffAlert.supplier_order_id, order_ids,
        ),
        "sales invoices": _delete_matching(SalesInvoice, SalesInvoice.job_order_id, job_ids),
        "audit rows": _delete_audit(job_ids, op_ids),
        "tool events": _delete_matching(ToolEvent, ToolEvent.id, event_ids),
        "purchase lines": _delete_matching(MaterialPurchase, MaterialPurchase.id, purchase_ids),
        "supplier orders for these jobs": len(exclusive_order_ids),
        "consumable restock orders": len(restock_ids),
        "receive events": len(receive_events),
        "adjust events": len(adjusts),
    }
    counts["supplier orders"] = _delete_matching(SupplierOrder, SupplierOrder.id, order_ids)
    _delete_matching(JobOperation, JobOperation.id, op_ids, rework_first=True)
    counts["job orders deleted"] = _delete_matching(JobOrder, JobOrder.id, job_ids)
    db.session.flush()
    return {"counts": counts, "stock": stock}


def _signed_stock_delta(event):
    """How this event changed quantity on hand.

    Newer rows store that in stock_delta. The old demo receives and the orphan
    adjust only stored a positive quantity: a receive added that many, and the
    adjust added its quantity.
    """
    if event.stock_delta is not None:
        return Decimal(str(event.stock_delta))
    qty = Decimal(str(event.quantity or 0))
    if event.type in (ToolEventType.RECEIVE, ToolEventType.ADJUST):
        return qty
    return Decimal("0")


def _delete_matching(model, column, ids, rework_first=False):
    ids = [item for item in ids if item]
    if not ids:
        return 0
    total = 0
    if rework_first:
        for chunk in _chunks(ids):
            total += (
                model.query.filter(
                    column.in_(chunk),
                    model.rework_of_operation_id.isnot(None),
                ).delete(synchronize_session=False)
                or 0
            )
    for chunk in _chunks(ids):
        total += model.query.filter(column.in_(chunk)).delete(synchronize_session=False) or 0
    return total


def _delete_or(model, column_a, ids_a, column_b, ids_b):
    total = _delete_matching(model, column_a, ids_a)
    total += _delete_matching(model, column_b, ids_b)
    return total


def _delete_audit(job_ids, op_ids):
    total = 0
    if job_ids:
        for chunk in _chunks(job_ids):
            total += (
                AuditLog.query.filter(
                    AuditLog.entity_type == "JobOrder",
                    AuditLog.entity_id.in_(chunk),
                ).delete(synchronize_session=False)
                or 0
            )
    if op_ids:
        for chunk in _chunks(op_ids):
            total += (
                AuditLog.query.filter(
                    AuditLog.entity_type == "JobOperation",
                    AuditLog.entity_id.in_(chunk),
                ).delete(synchronize_session=False)
                or 0
            )
    return total


def format_summary(summary):
    """Lines for the flask command."""
    if "deleted" in summary:
        lines = ["Would delete:" if not summary.get("applied") else "Deleted:"]
        for table, count in sorted(summary["deleted"].items()):
            if count:
                lines.append(f"  {table}: {count}")
        lines.append(f"  stock quantities restored: {summary['restoredStock']}")
        lines.append(f"  ledger rows cleared: {summary['ledgerRows']}")
        return lines

    lines = [
        f"Jobs: {summary['jobs']}",
        "  by status: " + _fmt_counts(summary["jobsByStatus"]),
        "  by type: " + _fmt_counts(summary["jobsByType"]),
        "  by client: " + _fmt_counts(summary["jobsByClient"]),
        f"Operations: {summary['operations']} (rework {summary['rework']})",
        "Pauses: " + _fmt_counts(summary["pausesByReason"]),
        f"Breakdowns: {summary['breakdowns']}",
        f"Attendance rows: {summary['attendance']}",
        f"Purchase orders: {summary['purchaseOrders']}",
    ]
    for row in summary["posBySupplier"]:
        lines.append(
            f"  {row['name']}: {row['orders']} orders, "
            f"{row['onTimePct'] if row['onTimePct'] is not None else 'n/a'}% on time "
            f"({row['onTime']} on time, {row['late']} late, {row['open']} not yet due)"
        )
    lines.append(f"Consumable purchases: {summary['consumablePurchases']}")
    lines.append(
        f"Tool borrows: {summary['toolBorrows']}, returns: {summary['toolReturns']}, "
        f"borrows with no return event: {summary['toolUnitsOpenBorrow']}"
    )
    lines.append("Tool unit custody was not changed. Every borrow has a return the same day.")
    if summary["holidaysAdded"]:
        lines.append("Holidays added: " + ", ".join(summary["holidaysAdded"]))
    else:
        lines.append("Holidays added: none (each date already had a calendar row)")
    if summary["holidaysAlreadyPresent"]:
        lines.append(
            "Holidays already on the calendar: "
            + ", ".join(summary["holidaysAlreadyPresent"])
        )
    lines.append(f"Stock snapshots: {summary['stockSnapshots']}")
    return lines


def _generate(start, end, seed):
    rng = random.Random(seed)
    before = _master_fingerprint()
    ledger = _Ledger()
    holidays_added, holidays_present = _ensure_holidays(ledger)
    db.session.flush()
    clear_calendar_cache()

    shop = _Shop.load(start, end)
    if not shop.workers:
        raise ValueError(
            "No production worker with a skill on an active machine. "
            "Disabled accounts are left out; invited workers are used as they are."
        )
    if not shop.clients:
        raise ValueError("No clients to attach job orders to.")
    if not shop.suppliers:
        raise ValueError("No active supplier with a PO code.")
    if not shop.catalog:
        raise ValueError("No active material catalog items.")
    if not shop.op_types:
        raise ValueError(
            "No in-house operation type with an active unit and a skilled worker."
        )
    if not shop.admins:
        raise ValueError("No active admin to record the history against.")
    if not shop.consumables:
        raise ValueError("No active consumable items.")

    shop.tune_efficiency(rng)
    plans = _plan_jobs(rng, shop, start, end)
    ops_guess = max(1, len(plans) * 2.2)
    capacity = max(1, len(shop.workers)) * 7.5 * max(1, len(shop.working_days))
    # Target hours per operation so the shop is busy but not full (about a third
    # of worker hours on job work; the rest is setup, breaks and slack).
    shop.hours_per_op = min(5.0, max(1.0, (capacity * 0.33) / ops_guess))
    print(f"Scheduling {len(plans)} job orders...", flush=True)
    counters = _schedule_jobs(rng, shop, ledger, plans)
    _attendance(rng, shop, ledger, plans, counters)
    _consumables(rng, shop, ledger, counters)
    _tool_borrows(rng, shop, ledger, counters)
    db.session.flush()
    after = _master_fingerprint()
    if before != after:
        raise RuntimeError(
            "Sample history changed a master row (people, clients, suppliers, "
            "machines, catalog, tool custody, skills, or shop settings). Rolled back."
        )
    _reject_notifications()
    return _summary(plans, counters, holidays_added, holidays_present, ledger)


def _plan_jobs(rng, shop, start, end):
    """More jobs, and larger ones, as the months go on, with a seasonal wobble.

    The slope is strong enough that a trend forecast beats a flat one, and
    the seasonal factors keep a mid-year dip from being a straight line.
    """
    weeks = _weeks(shop.working_days)
    span = max(1, (end.year - start.year) * 12 + (end.month - start.month))
    plans = []
    seq = 0
    for week in weeks:
        anchor = week[0]
        month_i = min(span, max(0, (anchor.year - start.year) * 12 + (anchor.month - start.month)))
        season = {3: 1.08, 4: 0.97, 5: 1.03, 6: 0.90, 7: 1.05, 8: 1.08, 9: 0.96, 10: 1.12}.get(
            anchor.month, 1.0
        )
        # About 32 job orders a month at the start, rising to about 38
        # (the shop's 30-40 a month), with the seasonal wobble.
        full = (7.5 + month_i * 0.17) * season * rng.uniform(0.94, 1.06)
        count = max(1, int(round(full * (len(week) / 6))))
        for _ in range(count):
            seq += 1
            po = week[rng.randrange(len(week))]
            client = shop.clients[_weighted(rng, shop.client_weights)]
            kind = _job_type(rng)
            needs = _needs_material(rng, kind)
            part, blurb = PARTS[rng.randrange(len(PARTS))]
            size = f"{rng.choice((20, 35, 50, 80, 120))} mm"
            month_i_job = min(span, max(0, (po.year - start.year) * 12 + (po.month - start.month)))
            sale_season = {
                1: 0.94, 2: 0.97, 3: 1.06, 4: 0.95, 5: 1.0, 6: 0.94,
                7: 1.03, 8: 1.08, 9: 0.96, 10: 1.12, 11: 1.04, 12: 0.98,
            }[po.month]
            # Most jobs are small; a few large ones (log-uniform), so monthly
            # sales land around PHP 3.5-5 million.
            raw = math.exp(rng.uniform(math.log(20000), math.log(210000)))
            amount = min(
                400000.0,
                max(6000.0, raw * (1 + 0.006 * month_i_job) * sale_season * rng.uniform(0.94, 1.06)),
            )
            due = shop.next_working(po + timedelta(days=rng.randint(7, 21)))
            plans.append(
                {
                    "seq": seq,
                    "po": po,
                    "due": due,
                    "client": client,
                    "kind": kind,
                    "needs": needs,
                    "title": _job_title(kind, part, size),
                    "description": f"{blurb} Size {size}.",
                    "qty": rng.randint(1, 12),
                    "amount": round(amount, 2),
                    "priority": _priority(rng),
                    "fate": "run",
                    "supplier_index": 0,
                }
            )
    plans.sort(key=lambda p: (p["po"], p["seq"]))
    if (end - start).days >= 28 and len(plans) >= len(OPEN_FATES):
        for plan, fate in zip(plans[-len(OPEN_FATES) :], OPEN_FATES):
            plan["fate"] = fate
        plans[-len(OPEN_FATES)].update(needs=True)  # first draft: to order
        plans[-len(OPEN_FATES) + 1].update(needs=False)
        plans[-len(OPEN_FATES) + 2].update(needs=True)  # waiting on a delivery
    for plan in plans:
        if plan["fate"] == "run" and rng.random() < 0.03:
            plan["fate"] = "cancelled"
    material_i = 0
    for plan in plans:
        if plan["needs"] and plan["fate"] != "cancelled":
            plan["supplier_index"] = material_i % len(shop.suppliers)
            material_i += 1
    return plans


def _schedule_jobs(rng, shop, ledger, plans):
    counters = _blank_counters()
    _plant_downtime(rng, shop, ledger, plans, counters)
    office = shop.office or shop.admins[0]
    admin = shop.admins[0]
    for index, plan in enumerate(plans, start=1):
        if index % 40 == 0:
            print(f"  {index}/{len(plans)}", flush=True)
        _one_job(rng, shop, ledger, plan, counters, admin, office)
    return counters


def _one_job(rng, shop, ledger, plan, counters, admin, office):
    encode = shop_local_to_utc(plan["po"], time(8, 30))
    fate = plan["fate"]
    kind = plan["kind"]
    condition = (
        PartCondition.RAW_MATERIAL
        if kind == JobType.FABRICATION or (kind == JobType.MACHINING and plan["needs"])
        else PartCondition.CLIENT_SUPPLIED_ITEM
    )
    material_status = MaterialStatus.NOT_REQUIRED
    if plan["needs"] and fate == "draft":
        material_status = MaterialStatus.TO_ORDER
    elif plan["needs"] and fate == "waiting":
        material_status = MaterialStatus.ORDERED
    elif plan["needs"] and fate != "cancelled":
        material_status = MaterialStatus.RECEIVED

    status = {
        "draft": JobOrderStatus.DRAFT,
        "cancelled": JobOrderStatus.CANCELLED,
        "progress": JobOrderStatus.IN_PROGRESS,
        "run": JobOrderStatus.DELIVERED,
        "scheduled": JobOrderStatus.SCHEDULED,
        "waiting": JobOrderStatus.SCHEDULED,
    }[fate]

    raw_materials = []
    catalog = None
    if plan["needs"] and fate != "cancelled":
        catalog = shop.catalog[rng.randrange(len(shop.catalog))]
        raw_materials = [
            {
                "id": _new_id(rng),
                "materialCatalogId": catalog.id,
                "name": catalog.name,
                "sizeSpec": _stock_size(plan["title"]),
                "quantity": float(plan["qty"]),
                "unit": catalog.default_unit,
            }
        ]

    job = JobOrder(
        id=_new_id(rng),
        client_id=plan["client"].id,
        title=plan["title"],
        description=plan["description"],
        due_date=plan["due"],
        client_po_number=_client_po(plan),
        po_date=plan["po"],
        status=status,
        priority=plan["priority"],
        job_type=kind,
        part_condition=condition,
        quantity=Decimal(plan["qty"]),
        unit_of_measure="pcs",
        amount=Decimal(str(plan["amount"])),
        raw_materials=raw_materials,
        material_status=material_status,
        created_by_id=admin.id,
        at_risk_alerted=False,
        created_at=encode,
        updated_at=encode,
    )
    if fate == "cancelled":
        reason, note = rng.choice(
            (
                ("CLIENT_CANCELLED", "Client withdrew the order"),
                ("DUPLICATE_OR_ENCODING_ERROR", "Duplicate of another order"),
                ("OTHER", "Order stopped before release"),
            )
        )
        job.cancelled_at = encode + timedelta(hours=3)
        job.cancelled_by_id = admin.id
        job.cancel_reason = reason
        job.cancel_note = note
        job.updated_at = job.cancelled_at
    ledger.add(job, encode)
    _audit(ledger, rng, admin, "CREATE", "JobOrder", job.id, encode, {"status": "DRAFT"})
    counters["jobs"] += 1
    counters["by_status"][status.value] += 1
    counters["by_type"][kind.value] += 1
    counters["by_client"][plan["client"].name] += 1
    if len(counters["titles"]) < 8:
        counters["titles"].append(plan["title"])

    if fate in ("cancelled", "draft"):
        return

    purchase = _purchase_for_job(rng, shop, ledger, plan, job, catalog, office, counters)
    not_before, previous_start, late = _ready_at(shop, plan, purchase)
    if fate == "progress":
        anchor = shop.working_days[-10] if len(shop.working_days) >= 10 else plan["po"]
        not_before = max(not_before, shop_local_to_utc(anchor, time(8, 0)))
        if purchase and purchase["received"] and purchase["received"] > anchor:
            not_before = shop_local_to_utc(purchase["received"], time(8, 0))
    elif fate in ("scheduled", "waiting"):
        not_before = max(not_before, shop_local_to_utc(shop.next_working(shop.end + timedelta(days=1)), time(8, 0)))

    # Work already started must have started by the end date, so in-progress
    # jobs are placed inside the history too (never in the future).
    horizon = shop.history_end if fate in ("run", "progress") else shop.future_end
    ops = _place_operations(
        rng, shop, ledger, job, plan, not_before, horizon, admin, counters, fate
    )
    if not ops:
        raise RuntimeError(f"Could not schedule {job.title} ({plan['po'].isoformat()}).")

    first = ops[0]
    job.released_at = min(encode + timedelta(hours=2), first["start"] - timedelta(minutes=30))
    if job.released_at < encode:
        job.released_at = encode
    _audit(
        ledger, rng, admin, "UPDATE", "JobOrder", job.id, job.released_at, {"status": "SCHEDULED"}
    )

    if late and previous_start and first["start"] > previous_start and fate == "run":
        capped = _cap_delay_start(
            shop, previous_start, first["start"], purchase.get("extra") or 1
        )
        if capped is not None:
            move = ScheduleMove(
                id=_new_id(rng),
                job_order_id=job.id,
                kind=DelayKind.MATERIAL,
                previous_start=capped,
                new_start=first["start"],
                reason="Supplier delivered after the promised date",
                supplier_order_id=purchase["order"].id if purchase else None,
                supplier_id=purchase["supplier"].id if purchase else None,
                material_cause=MaterialCause.SUPPLIER_LATE,
                moved_at=first["start"],
                moved_by_id=admin.id,
            )
            ledger.add(move, first["start"])
            job.material_delay_original_start = capped
            job.material_delay_reason = move.reason
            job.material_delay_supplier_order_id = move.supplier_order_id
            job.material_delayed_at = first["start"]
            job.delay_kind = DelayKind.MATERIAL
            counters["material_moves"] += 1
            counters["material_hours"] += shop_working_hours([(capped, first["start"])])

    if purchase and purchase["received"] and fate != "waiting":
        job.material_received_date = purchase["received"]
        job.material_expected_date = purchase["expected"]
        job.supplier_id = purchase["supplier"].id
        job.supplier_reference = purchase["order"].po_number
        if first.get("actual_start"):
            purchase["line"].consumed_at = first["actual_start"]
    elif purchase and fate == "waiting":
        job.material_expected_date = purchase["expected"]
        job.supplier_id = purchase["supplier"].id
        job.supplier_reference = purchase["order"].po_number

    finished = [op for op in ops if op["actual_end"] is not None]
    if fate == "run" and finished and all(op["actual_end"] for op in ops):
        last_end = max(op["actual_end"] for op in ops)
        last_day = utc_to_shop(last_end).date()
        if last_day >= shop.end - timedelta(days=1):
            job.status = JobOrderStatus.COMPLETED
            job.updated_at = last_end
            counters["by_status"][JobOrderStatus.DELIVERED.value] -= 1
            counters["by_status"][JobOrderStatus.COMPLETED.value] += 1
        else:
            delivered = shop.next_working(last_day + timedelta(days=1))
            job.for_delivery_at = last_end
            job.delivered_at = shop_local_to_utc(delivered, time(9, 0))
            job.updated_at = job.delivered_at
            _audit(
                ledger,
                rng,
                admin,
                "UPDATE",
                "JobOrder",
                job.id,
                job.delivered_at,
                {"status": "DELIVERED"},
            )
    elif fate == "progress":
        job.updated_at = ops[-1]["start"]
    else:
        job.updated_at = job.released_at


def _purchase_for_job(rng, shop, ledger, plan, job, catalog, office, counters):
    if not plan["needs"] or plan["fate"] in ("cancelled", "draft") or catalog is None:
        return None
    supplier = shop.suppliers[plan["supplier_index"]]
    profile = SUPPLIER_PROFILES[min(plan["supplier_index"], len(SUPPLIER_PROFILES) - 1)]
    issue = plan["po"]
    lead = supplier.typical_lead_time_days or 7
    lead = min(30, max(1, int(lead)))
    expected = shop.next_working(issue + timedelta(days=lead))
    late = False if plan["fate"] == "waiting" else _supplier_is_late(counters, supplier.id, profile["late"])
    if plan["fate"] == "waiting":
        issue = shop.working_days[-2] if len(shop.working_days) >= 2 else plan["po"]
        expected = shop.next_working(shop.end + timedelta(days=6))
        received = None
        late = False
        status = SupplierOrderStatus.ISSUED
    elif plan["fate"] == "scheduled":
        received = shop.working_on_or_before(shop.end - timedelta(days=1))
        if received < issue:
            received = issue
        expected = min(expected, received)
        late = False
        status = SupplierOrderStatus.RECEIVED
    else:
        received = expected
        if late:
            received = shop.next_working(expected + timedelta(days=profile["extra"]))
        status = SupplierOrderStatus.RECEIVED

    issued_at = shop_local_to_utc(issue, time(9, 0))
    year = issue.year
    seq = shop.next_po_seq(supplier.id, year)
    order = SupplierOrder(
        id=_new_id(rng),
        po_seq=seq,
        po_year=year,
        po_number=format_po_number(supplier.code, year, seq),
        supplier_id=supplier.id,
        status=status,
        date_issued=issue,
        expected_delivery_date=expected,
        original_expected_delivery_date=expected,
        received_date=received,
        terms_of_payment="PDC",
        delivery_mode="DELIVERY",
        prepared_by_id=office.id,
        issued_by_id=office.id,
        printed_at=issued_at + timedelta(minutes=20),
        printed_by_id=office.id,
        print_count=1,
        created_at=issued_at,
        updated_at=issued_at if received is None else shop_local_to_utc(received, time(11, 0)),
    )
    ledger.add(order, issued_at)
    _audit(ledger, rng, office, "CREATE", "SupplierOrder", order.id, issued_at, {"po": order.po_number})
    line = MaterialPurchase(
        id=_new_id(rng),
        job_order_id=job.id,
        material_catalog_id=catalog.id,
        planned_material_id=(job.raw_materials or [{}])[0].get("id"),
        material_name=catalog.name,
        size_spec=_stock_size(plan["title"]),
        quantity=Decimal(str(max(plan["qty"], 1))),
        unit=catalog.default_unit or "pcs",
        unit_cost=Decimal(str(rng.randrange(80, 1800))),
        supplier_id=supplier.id,
        supplier_order_id=order.id,
        date_ordered=issue,
        date_received=received,
        created_at=issued_at,
        updated_at=order.updated_at,
    )
    ledger.add(line, issued_at)
    if rng.random() < 0.35 and len(shop.catalog) > 1:
        other = shop.catalog[rng.randrange(len(shop.catalog))]
        if other.id == catalog.id:
            other = shop.catalog[(shop.catalog.index(catalog) + 1) % len(shop.catalog)]
        extra_line = MaterialPurchase(
            id=_new_id(rng),
            job_order_id=job.id,
            material_catalog_id=other.id,
            material_name=other.name,
            size_spec=_stock_size(plan["title"]),
            quantity=Decimal(str(max(1, plan["qty"] // 2))),
            unit=other.default_unit or "pcs",
            unit_cost=Decimal(str(rng.randrange(80, 1800))),
            supplier_id=supplier.id,
            supplier_order_id=order.id,
            date_ordered=issue,
            date_received=received,
            created_at=issued_at,
            updated_at=order.updated_at,
        )
        ledger.add(extra_line, issued_at)
        materials = list(job.raw_materials or [])
        materials.append(
            {
                "id": _new_id(rng),
                "materialCatalogId": other.id,
                "name": other.name,
                "sizeSpec": extra_line.size_spec,
                "quantity": float(extra_line.quantity),
                "unit": extra_line.unit,
            }
        )
        job.raw_materials = materials
    bucket = counters["suppliers"][supplier.id]
    bucket["name"] = supplier.name
    bucket["orders"] += 1
    if received is None:
        bucket["open"] += 1
    elif received <= expected:
        bucket["on_time"] += 1
    else:
        bucket["late"] += 1
    counters["purchase_orders"] += 1
    return {
        "order": order,
        "line": line,
        "supplier": supplier,
        "expected": expected,
        "received": received,
        "late": late and received is not None and received > expected,
        "extra": profile["extra"] if late else 0,
    }


def _ready_at(shop, plan, purchase):
    ready = shop.next_working(plan["po"])
    previous = None
    late = False
    if purchase and purchase["received"]:
        ready = max(ready, purchase["received"])
        if purchase["late"]:
            previous = shop_local_to_utc(purchase["expected"], time(8, 0))
            late = True
    elif purchase and purchase["received"] is None:
        ready = purchase["expected"]
    return shop_local_to_utc(ready, time(8, 0)), previous, late


def _place_operations(rng, shop, ledger, job, plan, not_before, horizon, admin, counters, fate):
    count = _op_count(rng)
    if fate == "progress":
        count = max(2, count)
    hours_each = shop.hours_per_op * rng.uniform(0.75, 1.25)
    placed = []
    cursor = not_before
    seq = {"n": 1}

    def take_seq():
        n = seq["n"]
        seq["n"] += 1
        return n

    for step in range(1, count + 1):
        op_type = shop.op_types[rng.randrange(len(shop.op_types))]
        estimated = max(0.75, min(8.0, hours_each))
        is_last = step == count
        in_progress = fate == "progress" and is_last
        pause = fate == "run" and not in_progress and rng.random() < 0.55
        result = _place_one(
            rng,
            shop,
            ledger,
            job,
            op_type,
            take_seq(),
            estimated,
            cursor,
            horizon,
            admin,
            counters,
            in_progress=in_progress,
            pause=pause,
            rework_of=None,
            take_seq=take_seq,
        )
        if result is None and horizon == shop.history_end:
            result = _place_one(
                rng,
                shop,
                ledger,
                job,
                op_type,
                seq["n"] - 1,
                max(0.75, estimated / 2),
                cursor,
                shop.future_end,
                admin,
                counters,
                in_progress=in_progress,
                pause=False,
                rework_of=None,
                take_seq=take_seq,
            )
        if result is None:
            return []
        placed.append(result)
        cursor = result["end"]
        if (
            not in_progress
            and result["actual_end"] is not None
            # About 3 in 100 operations need redoing, spread through the year.
            and rng.random() < 0.03
        ):
            redo = _place_one(
                rng,
                shop,
                ledger,
                job,
                op_type,
                take_seq(),
                max(1.5, min(2.5, estimated)),
                result["actual_end"],
                shop.future_end,
                admin,
                counters,
                in_progress=False,
                pause=False,
                rework_of=result,
                take_seq=take_seq,
            )
            if redo is not None:
                placed.append(redo)
                cursor = redo["end"]
        _remember_work(shop, result)
    return placed


def _place_one(
    rng,
    shop,
    ledger,
    job,
    op_type,
    seq_no,
    estimated,
    not_before,
    horizon,
    admin,
    counters,
    *,
    in_progress,
    pause,
    rework_of,
    take_seq,
):
    pairs = shop.pairs.get(op_type.default_machine_type_id) or []
    if not pairs:
        return None
    chosen = _choose_pause(rng, counters) if pause else None
    pause_len = chosen[2] if chosen else 0.0
    lead = pairs[0][1]
    if rework_of is not None:
        lead = rework_of["worker"]
        pairs = [(rework_of["unit"], lead)] + [p for p in pairs if p[1].id != lead.id]
    else:
        preferred = [p for p in pairs if p[0].default_operator_id == p[1].id]
        rest = [p for p in pairs if p not in preferred]
        if rest:
            cut = rng.randrange(len(rest))
            rest = rest[cut:] + rest[:cut]
        pairs = preferred + rest

    start = end = unit = worker = None
    worked = float(estimated)
    for unit_try, worker_try in pairs[:8]:
        worked_try = _paced_hours(
            shop, rng, worker_try, op_type.default_machine_type_id, estimated
        )
        duration_hours = estimated if in_progress else worked_try + pause_len
        windows = shop.windows[worker_try.id]
        busy = shop.worker_busy[worker_try.id] + shop.unit_busy[unit_try.id]
        start, end, _ = place_unbroken(
            windows,
            busy,
            timedelta(hours=duration_hours),
            not_before,
            horizon,
        )
        if start is not None:
            unit, worker = unit_try, worker_try
            worked = worked_try
            break
        start = end = None
    if start is None:
        return None

    worked_hours = worked if not in_progress else None
    actual_start = start
    actual_end = None if in_progress else end
    status = OperationStatus.IN_PROGRESS if in_progress else OperationStatus.COMPLETED
    if rework_of is None and in_progress is False and actual_end and utc_to_shop(start).date() > shop.end and job.status == JobOrderStatus.DELIVERED:
        status = OperationStatus.COMPLETED

    category = None
    reason_text = None
    if rework_of is not None:
        category, reason_text = _pick_rework(rng)
        status = OperationStatus.COMPLETED

    # Released-but-not-started operations (scheduled / waiting).
    if job.status == JobOrderStatus.SCHEDULED:
        status = OperationStatus.SCHEDULED
        actual_start = None
        actual_end = None
        worked_hours = None

    op = JobOperation(
        id=_new_id(rng),
        job_order_id=job.id,
        sequence_no=seq_no if rework_of is None else seq_no,
        operation_name=op_type.name,
        operation_type_id=op_type.id,
        machine_type_id=op_type.default_machine_type_id,
        machine_unit_id=unit.id,
        assigned_worker_id=worker.id,
        estimated_hours=Decimal(f"{estimated:.2f}"),
        scheduled_start=start,
        scheduled_end=end,
        actual_start=actual_start,
        actual_end=actual_end,
        status=status,
        rework_of_operation_id=rework_of["op"].id if rework_of else None,
        rework_reason=reason_text,
        rework_reason_category=category,
    )
    if worked_hours is not None and status == OperationStatus.COMPLETED:
        act = Decimal(f"{worked_hours:.4f}")
        est = op.estimated_hours
        op.actual_worked_hours = act
        op.variance_hours = act - est
        op.variance_pct = (op.variance_hours / est) * Decimal(100)
    ledger.add(op, start)
    _audit(ledger, rng, admin, "CREATE", "JobOperation", op.id, start, {"sequence": seq_no})
    shop.book(worker.id, unit.id, start, end)
    counters["operations"] += 1
    if rework_of is not None:
        counters["rework"] += 1
        counters["rework_hours"] += float(op.actual_worked_hours or 0)

    helper = _maybe_helper(rng, shop, op, worker, start, end, ledger)
    if status == OperationStatus.COMPLETED and actual_start and actual_end:
        _time_logs(
            rng,
            shop,
            ledger,
            op,
            worker,
            actual_start,
            actual_end,
            pause_len,
            chosen,
            counters,
            admin,
            take_seq,
        )
    elif status == OperationStatus.IN_PROGRESS and actual_start:
        _log(ledger, rng, op, worker, OperationTimeEvent.START, actual_start, None, None)

    return {
        "op": op,
        "start": start,
        "end": end,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "worker": worker,
        "unit": unit,
        "helper": helper,
        "rework": rework_of is not None,
    }


def _time_logs(rng, shop, ledger, op, worker, start, end, pause_len, chosen, counters, admin, take_seq):
    _log(ledger, rng, op, worker, OperationTimeEvent.START, start, None, None)
    if pause_len and chosen:
        pause_at = start + timedelta(minutes=20)
        resume_at = pause_at + timedelta(hours=pause_len)
        same_day = utc_to_shop(pause_at).date() == utc_to_shop(resume_at).date()
        if same_day and resume_at < end and utc_to_shop(start).hour <= 11:
            reason, note, _length = chosen
            _log(ledger, rng, op, worker, OperationTimeEvent.PAUSE, pause_at, reason, note)
            _log(ledger, rng, op, worker, OperationTimeEvent.RESUME, resume_at, None, None)
            counters["pauses"][reason.value] += 1
            counters["pause_hours"][reason.value] += pause_len
            if reason == OperationPauseReason.MACHINE_DOWN:
                _downtime(rng, shop, ledger, op, worker, pause_at, resume_at, counters, admin, take_seq)
    _log(ledger, rng, op, worker, OperationTimeEvent.COMPLETE, end, None, None)


def _downtime(rng, shop, ledger, op, worker, started, ended, counters, admin, take_seq):
    category, reason = DOWNTIME_REASONS[rng.randrange(len(DOWNTIME_REASONS))]
    row = MachineDowntime(
        id=_new_id(rng),
        machine_unit_id=op.machine_unit_id,
        started_at=started,
        ended_at=ended,
        category=category,
        reason=reason,
        reported_by_id=worker.id,
        job_order_id=op.job_order_id,
        operation_id=op.id,
        expected_repair_date=utc_to_shop(ended).date(),
        created_at=started,
    )
    ledger.add(row, started)
    shop.unit_busy[op.machine_unit_id].append((ensure_utc(started), ensure_utc(ended)))
    counters["breakdowns"] += 1
    others = [
        unit
        for unit in shop.units_by_type.get(op.machine_type_id, [])
        if unit.id != op.machine_unit_id
    ]
    if not others or rng.random() > 0.4:
        return
    other = others[rng.randrange(len(others))]
    skilled = [pair for pair in shop.pairs.get(op.machine_type_id, []) if pair[0].id == other.id]
    if not skilled:
        return
    unit, person = skilled[0]
    start, end, _ = place_unbroken(
        shop.windows[person.id],
        shop.worker_busy[person.id] + shop.unit_busy[unit.id],
        timedelta(hours=1),
        ended,
        shop.future_end,
    )
    if start is None:
        return
    follow = JobOperation(
        id=_new_id(rng),
        job_order_id=op.job_order_id,
        sequence_no=take_seq(),
        operation_name=op.operation_name,
        operation_type_id=op.operation_type_id,
        machine_type_id=op.machine_type_id,
        machine_unit_id=unit.id,
        assigned_worker_id=person.id,
        estimated_hours=Decimal("1.00"),
        scheduled_start=start,
        scheduled_end=end,
        actual_start=start,
        actual_end=end,
        actual_worked_hours=Decimal("1.0000"),
        variance_hours=Decimal("0"),
        variance_pct=Decimal("0"),
        status=OperationStatus.COMPLETED,
        notes="Continued on another unit after the stoppage",
    )
    ledger.add(follow, start)
    shop.book(person.id, unit.id, start, end)
    _log(ledger, rng, follow, person, OperationTimeEvent.START, start, None, None)
    _log(ledger, rng, follow, person, OperationTimeEvent.COMPLETE, end, None, None)
    counters["operations"] += 1
    counters["reassignments"] += 1
    _remember_work(
        shop,
        {
            "op": follow,
            "actual_start": start,
            "actual_end": end,
            "worker": person,
            "start": start,
            "end": end,
        },
    )


def _maybe_helper(rng, shop, op, lead, start, end, ledger):
    if rng.random() > 0.25:
        return None
    others = [w for w in shop.workers if w.id != lead.id]
    rng.shuffle(others)
    span = (ensure_utc(start), ensure_utc(end))
    for person in others[:4]:
        if not shop.worker_works(person.id, utc_to_shop(start).date()):
            continue
        if any(b[0] < span[1] and span[0] < b[1] for b in shop.worker_busy[person.id]):
            continue
        helper = OperationHelper(operation_id=op.id, worker_id=person.id, position=1)
        db.session.add(helper)
        ledger.note("operation_helpers", op.id, start, None)
        shop.worker_busy[person.id].append((ensure_utc(start), ensure_utc(end)))
        return person
    return None


def _remember_work(shop, result):
    start = result.get("actual_start") or result["start"]
    end = result.get("actual_end") or result["end"]
    if start is None:
        return
    day = utc_to_shop(start).date()
    last = utc_to_shop(end).date() if end is not None else shop.end
    worker = result["worker"]
    while day <= last and (day - utc_to_shop(start).date()).days < 12:
        if shop.is_working(day):
            shop.day_work[day].append((worker.id, result["op"].id, result["op"].job_order_id))
        day += timedelta(days=1)


def _attendance(rng, shop, ledger, plans, counters):
    """One row per production worker per working day, minus about 3 percent absences.

    Clock times follow that worker's own weekly hours (the shop default when
    they have no schedule). A few days run a little past the shift.
    """
    existing = {
        (worker_id, work_date)
        for worker_id, work_date in db.session.query(
            AttendanceRecord.worker_id, AttendanceRecord.work_date
        )
        .filter(
            AttendanceRecord.work_date >= shop.start,
            AttendanceRecord.work_date <= shop.end,
        )
        .all()
    }
    admin = shop.admins[0]
    crew = shop.crew or shop.workers
    for person in crew:
        for day in shop.working_days:
            if not shop.worker_works(person.id, day):
                continue
            if (person.id, day) in existing:
                continue
            if rng.random() < 0.03:
                counters["absences"] += 1
                continue
            sched = shop.worker_schedules.get(person.id) or {}
            source = sched if _has_hours(sched) else shop.shop_schedule
            start_t, end_t, working = effective_hours_for_date(day, source, shop.exceptions)
            if not working or start_t is None or end_t is None:
                continue
            overtime = rng.random() < 0.04
            clock_in = shop_local_to_utc(day, _shift_time(start_t, rng.randint(0, 8)))
            clock_out = shop_local_to_utc(
                day, _shift_time(end_t, 45 if overtime else rng.randint(0, 8))
            )
            if clock_out <= clock_in:
                clock_out = shop_local_to_utc(day, end_t)
            row = AttendanceRecord(
                id=_new_id(rng),
                worker_id=person.id,
                work_date=day,
                clock_in=clock_in,
                clock_out=clock_out,
                note="Overtime" if overtime else None,
                recorded_by_id=admin.id,
                created_at=clock_out,
                updated_at=clock_out,
            )
            ledger.add(row, clock_out)
            counters["attendance"] += 1


# Where cash purchases of consumables are made; not suppliers in the system.
CASH_STORES = ("Hardware store", "Industrial supply store", "Welding supply store")
# Typical days between purchases and amount bought, by how fast an item is used.
BUY_EVERY = {"high": 10, "mid": 20, "low": 42, "bulk": 60}
BUY_AMOUNT = {"high": 10, "mid": 6, "low": 3, "bulk": 1}
# Rough store prices in pesos per unit, so the sample amounts look like a real
# shop's (a pail of oil is not ₱34). Matched on the item name; first match wins.
SAMPLE_PRICES = (
    ("masking tape", 45), ("teflon tape", 30), ("penetrating oil", 280), ("leather welding gloves", 250),
    ("layout fluid", 450), ("parting blade", 650), ("shop rags", 60), ("spray paint", 150),
    ("thread locker", 380), ("welding lens", 80),
    ("hydraulic oil", 3200), ("cutting oil", 2800), ("way oil", 3000), ("gear oil", 3100),
    ("coolant", 2600), ("oil", 2800), ("grease", 650),
    ("carbide insert", 3500), ("carbide end mill", 1200), ("hss end mill", 450), ("end mill", 600),
    ("drill bit", 180), ("center drill", 150), ("tap", 350), ("reamer", 900),
    ("lathe tool bit", 220), ("tool bit", 220),
    ("bandsaw blade", 1500), ("hacksaw blade", 60),
    ("cutting disc", 45), ("grinding disc", 60), ("flap disc", 75), ("grinding stone", 350),
    ("cup wire brush", 180), ("wire brush", 120), ("emery cloth", 350), ("sandpaper", 25),
    ("mig wire", 1800), ("welding rod", 180), ("tungsten", 250), ("anti-spatter", 250),
    ("marker", 35), ("soapstone", 20),
    ("safety glasses", 120), ("ear plugs", 15), ("gloves", 25), ("face shield", 150),
)


def _sample_price(name):
    low = (name or "").lower()
    for word, price in SAMPLE_PRICES:
        if word in low:
            return Decimal(price)
    return Decimal(100)


def _consumables(rng, shop, ledger, counters):
    """Consumable purchases. Nothing is counted or scanned: most items are bought
    in cash and recorded by the office, and about one in ten goes on a supplier
    order. Each item has its own rhythm so the forecasts differ."""
    office = shop.office or shop.admins[0]
    names = sorted(shop.consumables, key=lambda t: t.name.lower())
    days = [d for d in shop.working_days if d <= shop.end]
    if not days:
        return
    home = {}
    for index, tool in enumerate(sorted(shop.consumables, key=lambda t: t.id)):
        home[tool.id] = shop.suppliers[index % len(shop.suppliers)] if shop.suppliers else None
    for index, tool in enumerate(names):
        rate = _usage_rate(tool.name)
        every = BUY_EVERY[rate] + (index % 4) * 3
        amount = BUY_AMOUNT[rate] + (0 if rate == "bulk" else (index % 3) * 2)
        cost = tool.unit_cost if tool.unit_cost is not None else _sample_price(tool.name)
        day = shop.next_working(days[0] + timedelta(days=rng.randrange(max(1, every))))
        while day <= shop.end:
            qty = Decimal(max(1, round(amount * rng.uniform(0.6, 1.4))))
            supplier = home[tool.id]
            if supplier is not None and rng.random() < 0.1:
                _order, received, _packed = _stock_po(
                    rng, shop, ledger, supplier, day, [(tool, qty)], office, counters
                )
                counters["consumable_purchases"] += 1 if received <= shop.end else 0
            else:
                when = shop_local_to_utc(day, time(15, 30))
                price = (Decimal(str(cost)) * Decimal(str(rng.uniform(0.9, 1.15)))).quantize(Decimal("0.01"))
                ledger.add(
                    ConsumablePurchase(
                        id=_new_id(rng),
                        tool_id=tool.id,
                        purchased_on=day,
                        quantity=qty,
                        unit=tool.unit or "pcs",
                        unit_cost=price,
                        bought_from=rng.choice(CASH_STORES),
                        recorded_by_id=office.id,
                        created_at=when,
                    ),
                    when,
                )
                counters["consumable_purchases"] += 1
            gap = max(2, round(every * rng.uniform(0.7, 1.3)))
            day = shop.next_working(day + timedelta(days=gap))


def _stock_po(rng, shop, ledger, supplier, day, rows, office, counters):
    """One purchase order for every item this supplier is replenishing this week."""
    lead = min(21, max(2, int(supplier.typical_lead_time_days or 7)))
    profile = SUPPLIER_PROFILES[min(shop.suppliers.index(supplier), len(SUPPLIER_PROFILES) - 1)]
    expected = shop.next_working(day + timedelta(days=lead))
    late = _supplier_is_late(counters, supplier.id, profile["late"])
    received = shop.next_working(expected + timedelta(days=profile["extra"])) if late else expected
    if received > shop.end:
        received = shop.end if shop.is_working(shop.end) else shop.working_on_or_before(shop.end)
        late = received > expected
    year = day.year
    seq = shop.next_po_seq(supplier.id, year)
    issued_at = shop_local_to_utc(day, time(10, 0))
    order = SupplierOrder(
        id=_new_id(rng),
        po_seq=seq,
        po_year=year,
        po_number=format_po_number(supplier.code, year, seq),
        supplier_id=supplier.id,
        status=SupplierOrderStatus.RECEIVED,
        date_issued=day,
        expected_delivery_date=expected,
        original_expected_delivery_date=expected,
        received_date=received,
        notes="consumable restock",
        terms_of_payment="PDC",
        delivery_mode="DELIVERY",
        prepared_by_id=office.id,
        issued_by_id=office.id,
        printed_at=issued_at,
        printed_by_id=office.id,
        print_count=1,
        created_at=issued_at,
        updated_at=shop_local_to_utc(received, time(11, 0)),
    )
    ledger.add(order, issued_at)
    _audit(ledger, rng, office, "CREATE", "SupplierOrder", order.id, issued_at, {"po": order.po_number})
    packed = []
    for tool, amount in rows:
        cost = tool.unit_cost if tool.unit_cost is not None else Decimal(str(rng.randrange(15, 400)))
        line = MaterialPurchase(
            id=_new_id(rng),
            tool_id=tool.id,
            material_name=tool.name,
            grade_or_spec=tool.size_spec,
            size_spec=tool.size_spec,
            quantity=amount,
            unit=tool.unit or "pcs",
            unit_cost=Decimal(str(cost)),
            supplier_id=supplier.id,
            supplier_order_id=order.id,
            date_ordered=day,
            date_received=received,
            created_at=issued_at,
            updated_at=order.updated_at,
        )
        ledger.add(line, issued_at)
        packed.append((tool, amount, line))
    bucket = counters["suppliers"][supplier.id]
    bucket["name"] = supplier.name
    bucket["orders"] += 1
    if received <= expected:
        bucket["on_time"] += 1
    else:
        bucket["late"] += 1
    counters["purchase_orders"] += 1
    return order, received, packed


def _tool_borrows(rng, shop, ledger, counters):
    units = [
        unit
        for unit in ToolUnit.query.order_by(ToolUnit.asset_code).all()
        if unit.status == ToolUnitStatus.AVAILABLE
    ]
    if not units or not shop.working_days or not shop.workers:
        return
    for day in shop.working_days:
        for index, unit in enumerate(units):
            if rng.random() < 0.08:
                continue
            person = shop.workers[(index + day.toordinal()) % len(shop.workers)]
            borrow_at = shop_local_to_utc(day, time(8, 10))
            borrow = ToolEvent(
                id=_new_id(rng),
                tool_unit_id=unit.id,
                worker_id=person.id,
                type=ToolEventType.BORROW,
                quantity=Decimal("1"),
                created_at=borrow_at,
            )
            ledger.add(borrow, borrow_at)
            counters["borrows"] += 1
            return_at = shop_local_to_utc(day, time(16, 40))
            returned = ToolEvent(
                id=_new_id(rng),
                tool_unit_id=unit.id,
                worker_id=person.id,
                type=ToolEventType.RETURN,
                quantity=Decimal("1"),
                created_at=return_at,
            )
            ledger.add(returned, return_at)
            counters["returns"] += 1


def _ensure_holidays(ledger):
    added = []
    present = []
    existing = {
        row.date: row
        for row in WorkCalendarException.query.filter(
            WorkCalendarException.date.in_([d for d, _ in HOLIDAYS])
        ).all()
    }
    for day, name in HOLIDAYS:
        if day in existing:
            present.append(day.isoformat())
            continue
        when = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        row = WorkCalendarException(
            id=str(uuid.uuid5(uuid.NAMESPACE_OID, f"sample-holiday-{day.isoformat()}")),
            date=day,
            type=CalendarExceptionType.HOLIDAY_NO_WORK,
            note=name,
        )
        ledger.add(row, when)
        added.append(day.isoformat())
    return added, present


def _remove():
    rows = SampleRecord.query.order_by(SampleRecord.id).all()
    snapshots = []
    created = defaultdict(list)
    for row in rows:
        detail = row.detail or {}
        if detail.get("role") == "stock_snapshot":
            snapshots.append((row.record_id, str(detail.get("quantityOnHand"))))
        else:
            created[row.table_name].append(row.record_id)
    unknown = set(created) - set(_DELETE_ORDER) - {"operation_helpers"}
    if unknown:
        raise RuntimeError("Ledger has tables remove does not know: " + ", ".join(sorted(unknown)))

    deleted = {}
    helper_ids = created.get("operation_helpers") or []
    deleted["operation_helpers"] = 0
    for chunk in _chunks(helper_ids):
        deleted["operation_helpers"] += (
            OperationHelper.query.filter(OperationHelper.operation_id.in_(chunk)).delete(
                synchronize_session=False
            )
            or 0
        )
    for table in _DELETE_ORDER:
        ids = created.get(table) or []
        deleted[table] = _delete_ids(table, ids) if ids else 0

    restored = 0
    for record_id, qty in snapshots:
        tool = db.session.get(Tool, record_id)
        if tool is None:
            continue
        tool.quantity_on_hand = Decimal(qty)
        restored += 1
    ledger_rows = len(rows)
    SampleRecord.query.delete(synchronize_session=False)
    return {"deleted": deleted, "restoredStock": restored, "ledgerRows": ledger_rows}


_DELETE_ORDER = (
    "consumable_purchases",
    "operation_time_logs",
    "schedule_moves",
    "tool_events",
    "machine_downtimes",
    "audit_logs",
    "notification_logs",
    "sales_invoices",
    "staff_alerts",
    "material_purchases",
    "supplier_orders",
    "operations",
    "attendance_records",
    "job_orders",
    "work_calendar_exceptions",
)

_DELETE_MODELS = {
    "consumable_purchases": ConsumablePurchase,
    "operation_time_logs": OperationTimeLog,
    "schedule_moves": ScheduleMove,
    "tool_events": ToolEvent,
    "machine_downtimes": MachineDowntime,
    "audit_logs": AuditLog,
    "notification_logs": NotificationLog,
    "sales_invoices": SalesInvoice,
    "staff_alerts": StaffAlert,
    "material_purchases": MaterialPurchase,
    "supplier_orders": SupplierOrder,
    "operations": JobOperation,
    "attendance_records": AttendanceRecord,
    "job_orders": JobOrder,
    "work_calendar_exceptions": WorkCalendarException,
}


class _Shop:
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.history_end = shop_local_to_utc(end, time(17, 0))
        self.future_end = shop_local_to_utc(end + timedelta(days=21), time(17, 0))
        self.clients = []
        self.client_weights = []
        self.suppliers = []
        self.catalog = []
        self.workers = []
        self.crew = []
        self.admins = []
        self.office = None
        self.op_types = []
        self.pairs = {}
        self.units_by_type = {}
        self.windows = {}
        self.worker_schedules = {}
        self.worker_busy = defaultdict(list)
        self.unit_busy = defaultdict(list)
        self.efficiency = {}
        self.machine_pace = {}
        self.working_days = []
        self.exceptions = {}
        self.shop_schedule = default_shop_schedule_by_dow()
        self.day_work = defaultdict(list)
        self.consumables = []
        self.hours_per_op = 2.0
        self._po_next = {}
        self.closing_qty = {}
        self.estimated_ids = set()

    @classmethod
    def load(cls, start, end):
        shop = cls(start, end)
        shop.clients = Client.query.order_by(Client.name, Client.id).all()
        weights = [0.40, 0.28, 0.20, 0.12]
        raw = [weights[i] if i < len(weights) else 0.05 for i in range(len(shop.clients))]
        total = sum(raw) or 1
        shop.client_weights = [w / total for w in raw]
        shop.suppliers = [
            s
            for s in Supplier.query.filter_by(active=True).order_by(Supplier.name, Supplier.id).all()
            if s.code
        ]
        shop.catalog = (
            MaterialCatalogItem.query.filter_by(active=True).order_by(MaterialCatalogItem.name).all()
        )
        shop.admins = (
            User.query.filter_by(role=UserRole.ADMIN, status=UserStatus.ACTIVE, active=True)
            .order_by(User.id)
            .all()
        )
        office = (
            User.query.filter_by(role=UserRole.OFFICE_STAFF, status=UserStatus.ACTIVE, active=True)
            .order_by(User.id)
            .first()
        )
        shop.office = office
        # Invited employees are the shop's crew until they activate an account.
        # Their rows are not changed.
        workers = (
            User.query.filter(
                User.role == UserRole.PRODUCTION_WORKER,
                User.status != UserStatus.DISABLED,
            )
            .order_by(User.id)
            .all()
        )
        skills = defaultdict(set)
        for row in WorkerSkill.query.all():
            skills[row.worker_id].add(row.machine_type_id)
        units = (
            MachineUnit.query.filter_by(active=True).order_by(MachineUnit.label).all()
        )
        shop.units_by_type = defaultdict(list)
        for unit in units:
            shop.units_by_type[unit.machine_type_id].append(unit)
        worker_ids = {w.id for w in workers}
        op_types = (
            OperationType.query.filter_by(active=True, is_outsourced=False)
            .order_by(OperationType.code)
            .all()
        )
        usable = []
        for op_type in op_types:
            type_id = op_type.default_machine_type_id
            if not type_id or not shop.units_by_type.get(type_id):
                continue
            skilled = [w for w in workers if type_id in skills.get(w.id, ())]
            if not skilled:
                continue
            usable.append(op_type)
            pairs = []
            for unit in shop.units_by_type[type_id]:
                preferred = [w for w in skilled if w.id == unit.default_operator_id]
                others = [w for w in skilled if w.id != unit.default_operator_id]
                for person in preferred + others:
                    pairs.append((unit, person))
            shop.pairs[type_id] = pairs
        shop.op_types = usable
        used_workers = []
        seen = set()
        for pairs in shop.pairs.values():
            for _unit, person in pairs:
                if person.id not in seen:
                    seen.add(person.id)
                    used_workers.append(person)
        shop.workers = used_workers or [w for w in workers if w.id in worker_ids and skills.get(w.id)]
        shop.crew = list(workers)
        span_start = start - timedelta(days=2)
        span_end = end + timedelta(days=21)
        shop.exceptions = load_calendar_exceptions(span_start, span_end)
        schedules = load_worker_schedule_maps_many([w.id for w in shop.crew])
        shop_windows = build_worker_working_windows(
            shop.shop_schedule,
            shop.exceptions,
            shop_local_to_utc(span_start, time(0, 0)),
            shop.future_end,
        )
        for person in shop.crew:
            shop.worker_schedules[person.id] = schedules.get(person.id) or {}
        for person in shop.workers:
            sched = shop.worker_schedules.get(person.id) or {}
            source = sched if _has_hours(sched) else shop.shop_schedule
            windows = build_worker_working_windows(
                source,
                shop.exceptions,
                shop_local_to_utc(span_start, time(0, 0)),
                shop.future_end,
            )
            shop.windows[person.id] = intersect_intervals(windows, shop_windows) or windows
        shop.working_days = [d for d in _daterange(start, end) if shop.is_working(d)]
        shop._load_busy(span_start)
        shop._load_po_seqs()
        shop.consumables = (
            Tool.query.filter_by(category=ToolCategory.CONSUMABLE, active=True)
            .order_by(Tool.name)
            .all()
        )
        capacity = max(1, len(shop.workers)) * 7.5 * max(1, len(shop.working_days))
        weeks = max(1, len({d.isocalendar()[:2] for d in shop.working_days}))
        ops = weeks * 12 * 2.2
        shop.hours_per_op = min(3.5, max(1.0, (capacity * 0.42) / ops))
        eff_rng = random.Random(len(shop.workers) * 1009 + start.toordinal())
        # Efficiency is stable per worker id but still depends on the run seed
        # via the caller. Re-seeded below in generate — set a placeholder first.
        for person in shop.workers:
            shop.efficiency[person.id] = 0.85 + eff_rng.random() * 0.40
        return shop

    def tune_efficiency(self, rng):
        self.machine_pace = {}
        for type_id in {op.default_machine_type_id for op in self.op_types}:
            self.machine_pace[type_id] = rng.uniform(0.96, 1.05)
        for person in self.workers:
            # actual ÷ target. The band is about 85–108 percent efficiency
            # per worker; hours weight the shop result into 88–98 percent.
            self.efficiency[person.id] = rng.uniform(0.93, 1.18)

    def is_working(self, day):
        return bool(effective_windows_for_date(day, self.shop_schedule, self.exceptions))

    def worker_works(self, worker_id, day):
        sched = self.worker_schedules.get(worker_id) or {}
        source = sched if _has_hours(sched) else self.shop_schedule
        return bool(effective_windows_for_date(day, source, self.exceptions))

    def next_working(self, day):
        for _ in range(40):
            if self.is_working(day):
                return day
            day += timedelta(days=1)
        return day

    def working_on_or_before(self, day):
        for _ in range(40):
            if self.is_working(day):
                return day
            day -= timedelta(days=1)
        return day

    def book(self, worker_id, unit_id, start, end):
        span = (ensure_utc(start), ensure_utc(end))
        self.worker_busy[worker_id].append(span)
        if unit_id:
            self.unit_busy[unit_id].append(span)

    def next_po_seq(self, supplier_id, year):
        key = (supplier_id, year)
        seq = self._po_next.get(key, 1)
        self._po_next[key] = seq + 1
        return seq

    def _load_po_seqs(self):
        rows = (
            db.session.query(
                SupplierOrder.supplier_id,
                SupplierOrder.po_year,
                func.max(SupplierOrder.po_seq),
            )
            .filter(SupplierOrder.po_year.isnot(None))
            .group_by(SupplierOrder.supplier_id, SupplierOrder.po_year)
            .all()
        )
        self._po_next = {(sid, year): int(mx or 0) + 1 for sid, year, mx in rows}

    def _load_busy(self, span_start):
        start_utc = shop_local_to_utc(span_start, time(0, 0))
        rows = (
            db.session.query(
                JobOperation.assigned_worker_id,
                JobOperation.machine_unit_id,
                JobOperation.scheduled_start,
                JobOperation.scheduled_end,
            )
            .filter(
                JobOperation.scheduled_start.isnot(None),
                JobOperation.scheduled_end.isnot(None),
                JobOperation.scheduled_end > start_utc,
                JobOperation.scheduled_start < self.future_end,
            )
            .all()
        )
        for worker_id, unit_id, start, end in rows:
            if worker_id:
                self.worker_busy[worker_id].append((ensure_utc(start), ensure_utc(end)))
            if unit_id:
                self.unit_busy[unit_id].append((ensure_utc(start), ensure_utc(end)))
        helpers = (
            db.session.query(
                OperationHelper.worker_id,
                JobOperation.scheduled_start,
                JobOperation.scheduled_end,
            )
            .join(JobOperation, JobOperation.id == OperationHelper.operation_id)
            .filter(
                JobOperation.scheduled_start.isnot(None),
                JobOperation.scheduled_end.isnot(None),
                JobOperation.scheduled_end > start_utc,
            )
            .all()
        )
        for worker_id, start, end in helpers:
            self.worker_busy[worker_id].append((ensure_utc(start), ensure_utc(end)))
        downs = MachineDowntime.query.filter(
            MachineDowntime.started_at < self.future_end,
            db.or_(
                MachineDowntime.ended_at.is_(None),
                MachineDowntime.ended_at > start_utc,
            ),
        ).all()
        for row in downs:
            end = row.ended_at or self.future_end
            self.unit_busy[row.machine_unit_id].append((ensure_utc(row.started_at), ensure_utc(end)))


class _Ledger:
    def __init__(self):
        self.snapshots = 0

    def add(self, obj, when):
        _stamp(obj, when)
        db.session.add(obj)
        self.note(obj.__tablename__, obj.id, when, None)

    def note(self, table, record_id, when, detail):
        db.session.add(
            SampleRecord(
                table_name=table,
                record_id=str(record_id),
                created_at=when,
                detail=detail,
            )
        )


def _log(ledger, rng, op, worker, kind, when, reason, note):
    row = OperationTimeLog(
        id=_new_id(rng),
        operation_id=op.id,
        worker_id=worker.id,
        event=kind,
        event_at=when,
        reason=reason,
        note=note,
        created_at=when,
    )
    ledger.add(row, when)


def _audit(ledger, rng, user, action, entity_type, entity_id, when, after):
    row = AuditLog(
        id=_new_id(rng),
        user_id=user.id,
        user_role=user.role.value if user.role else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        after_json=after,
        created_at=when,
    )
    ledger.add(row, when)


def _summary(plans, counters, holidays_added, holidays_present, ledger):
    pos = []
    for bucket in counters["suppliers"].values():
        decided = bucket["on_time"] + bucket["late"]
        pos.append(
            {
                "name": bucket["name"],
                "orders": bucket["orders"],
                "onTime": bucket["on_time"],
                "late": bucket["late"],
                "open": bucket["open"],
                "onTimePct": round(bucket["on_time"] / decided * 100, 1) if decided else None,
            }
        )
    pos.sort(key=lambda r: r["name"].lower())
    return {
        "jobs": counters["jobs"],
        "jobsByStatus": dict(counters["by_status"]),
        "jobsByType": dict(counters["by_type"]),
        "jobsByClient": dict(counters["by_client"]),
        "operations": counters["operations"],
        "rework": counters["rework"],
        "pausesByReason": dict(counters["pauses"]),
        "breakdowns": counters["breakdowns"],
        "reassignments": counters["reassignments"],
        "attendance": counters["attendance"],
        "absences": counters["absences"],
        "purchaseOrders": counters["purchase_orders"],
        "posBySupplier": pos,
        "consumablePurchases": counters["consumable_purchases"],
        "toolBorrows": counters["borrows"],
        "toolReturns": counters["returns"],
        "toolUnitsOpenBorrow": counters["open_borrows"],
        "holidaysAdded": holidays_added,
        "holidaysAlreadyPresent": holidays_present,
        "stockSnapshots": ledger.snapshots,
        "firstTitles": list(counters["titles"]),
        "materialMoves": counters["material_moves"],
        "materialHours": round(counters["material_hours"], 1),
        "downtimeHours": round(counters["downtime_hours"], 1),
        "reworkHours": round(counters["rework_hours"], 1),
    }


def _blank_counters():
    return {
        "jobs": 0,
        "by_status": defaultdict(int),
        "by_type": defaultdict(int),
        "by_client": defaultdict(int),
        "operations": 0,
        "rework": 0,
        "pauses": defaultdict(int),
        "breakdowns": 0,
        "reassignments": 0,
        "attendance": 0,
        "absences": 0,
        "purchase_orders": 0,
        "suppliers": defaultdict(lambda: {"name": "", "orders": 0, "on_time": 0, "late": 0, "open": 0}),
        "consumable_purchases": 0,
        "borrows": 0,
        "returns": 0,
        "open_borrows": 0,
        "titles": [],
        "material_moves": 0,
        "material_hours": 0.0,
        "downtime_hours": 0.0,
        "rework_hours": 0.0,
        "pause_hours": defaultdict(float),
        "delay_target_material": 0.0,
    }


def _master_fingerprint():
    def rows(query, *cols):
        return tuple(tuple(getattr(obj, col) for col in cols) for obj in query.all())

    settings = db.session.get(ShopSettings, 1)
    setting_key = None
    if settings is not None:
        setting_key = (
            str(settings.break_start),
            str(settings.break_end),
            settings.reorder_soon_working_days,
        )
    return (
        rows(Client.query.order_by(Client.id), "id", "name", "email", "notify_by_email"),
        rows(User.query.order_by(User.id), "id", "full_name", "role", "status", "active"),
        rows(
            Supplier.query.order_by(Supplier.id),
            "id",
            "name",
            "code",
            "typical_lead_time_days",
            "active",
        ),
        rows(MachineType.query.order_by(MachineType.id), "id", "code", "name"),
        rows(
            MachineUnit.query.order_by(MachineUnit.id),
            "id",
            "label",
            "active",
            "default_operator_id",
        ),
        rows(MaterialCatalogItem.query.order_by(MaterialCatalogItem.id), "id", "name", "active"),
        rows(Tool.query.order_by(Tool.id), "id", "name", "code", "unit_cost", "minimum_stock", "active"),
        tuple(
            (u.id, u.status, u.current_holder_id, u.held_since, u.asset_code)
            for u in ToolUnit.query.order_by(ToolUnit.id).all()
        ),
        rows(
            WorkerSkill.query.order_by(WorkerSkill.id),
            "id",
            "worker_id",
            "machine_type_id",
            "proficiency",
        ),
        tuple(
            (s.worker_id, s.day_of_week, s.is_working, str(s.start_time), str(s.end_time))
            for s in WorkerSchedule.query.order_by(WorkerSchedule.id).all()
        ),
        setting_key,
    )


def _reject_notifications():
    # Sample clients are not given email consent, and this command never
    # calls the mailer. A log row would mean something else sent one.
    if NotificationLog.query.filter(NotificationLog.id.isnot(None)).count():
        # Existing logs from before this run are fine. Only rows we just
        # added would be pending in the session without a ledger note.
        pending = [
            obj
            for obj in db.session.new
            if isinstance(obj, NotificationLog)
        ]
        if pending:
            raise RuntimeError("Sample history tried to write a notification log.")


def _delete_ids(table, ids):
    model = _DELETE_MODELS[table]
    total = 0
    if table == "operations":
        for chunk in _chunks(ids):
            total += (
                model.query.filter(
                    model.id.in_(chunk),
                    model.rework_of_operation_id.isnot(None),
                ).delete(synchronize_session=False)
                or 0
            )
    for chunk in _chunks(ids):
        total += model.query.filter(model.id.in_(chunk)).delete(synchronize_session=False) or 0
    return total


def _chunks(ids, size=400):
    for index in range(0, len(ids), size):
        yield ids[index : index + size]


def _stamp(obj, when):
    if getattr(obj, "created_at", None) is None and hasattr(obj, "created_at"):
        obj.created_at = when
    if hasattr(obj, "updated_at") and obj.updated_at is None:
        obj.updated_at = when


def _new_id(rng) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _parse_day(value, name):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _daterange(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _weeks(days):
    buckets = []
    current = []
    key = None
    for day in days:
        iso = day.isocalendar()[:2]
        if key is None:
            key = iso
        elif iso != key:
            buckets.append(current)
            current = []
            key = iso
        current.append(day)
    if current:
        buckets.append(current)
    return buckets


def _weighted(rng, weights):
    roll = rng.random()
    acc = 0.0
    for index, weight in enumerate(weights):
        acc += weight
        if roll <= acc:
            return index
    return len(weights) - 1


def _job_title(kind, part, size):
    """Titles in the shop's own style, e.g. "Fabrication of Drive shaft, 50 mm"."""
    verb = {
        JobType.FABRICATION: "Fabrication of",
        JobType.MACHINING: "Machining of",
        JobType.REPAIR: "Repair of",
    }.get(kind, "Fabrication of")
    return f"{verb} {part}, {size}"


def _client_po(plan):
    """A plain numeric client PO number, like the paper POs the shop receives."""
    return str(12000 + plan["seq"] * 7 + (plan["po"].toordinal() % 5))


def _job_type(rng):
    roll = rng.random()
    if roll < 0.55:
        return JobType.FABRICATION
    if roll < 0.80:
        return JobType.MACHINING
    return JobType.REPAIR


def _needs_material(rng, kind):
    # Most jobs run from stock or client-supplied parts. A purchase is the
    # exception, so job POs stay near a couple per week.
    if kind == JobType.FABRICATION:
        return rng.random() < 0.28
    if kind == JobType.MACHINING:
        return rng.random() < 0.08
    return rng.random() < 0.02


def _priority(rng):
    roll = rng.random()
    if roll < 0.20:
        return JobPriority.HIGH
    if roll < 0.90:
        return JobPriority.MODERATE
    return JobPriority.LOW


def _op_count(rng):
    roll = rng.random()
    if roll < 0.20:
        return 1
    if roll < 0.60:
        return 2
    if roll < 0.85:
        return 3
    return 4


def _supplier_is_late(counters, supplier_id, rate):
    """A fixed share of each supplier's orders, spread through the run."""
    bucket = counters["suppliers"][supplier_id]
    bucket["draws"] = bucket.get("draws", 0) + 1
    return (bucket["draws"] * 17) % 100 < int(round(rate * 100))


def _choose_pause(rng, counters):
    """The pause cause that is furthest under its share of the delay budget."""
    base = max(float(counters.get("delay_target_material") or 0), float(counters["material_hours"]), 1.0)
    best = None
    gap = 0.0
    for reason, share, length, note in PAUSE_TARGETS:
        short = base * share - counters["pause_hours"][reason.value]
        if short > gap:
            gap = short
            best = (reason, note, length)
    if best is None or gap < best[2]:
        return None
    return best


def _paced_hours(shop, rng, worker, machine_type_id, estimated):
    """Actual hours. Overall target/actual lands near 93 percent.

    Each worker sits between about 0.82 and 1.10 (target ÷ actual), and the
    machine type nudges that without leaving the band.
    """
    pace = shop.efficiency.get(worker.id, 1.06)
    pace *= shop.machine_pace.get(machine_type_id, 1.0)
    pace = min(1.22, max(0.91, pace))
    pace *= rng.uniform(0.98, 1.02)
    return max(0.5, float(estimated) * pace)


def _cap_delay_start(shop, expected_morning, actual_start, extra_days):
    """Keep a late delivery's lost time to the lateness itself, at most two shop days.

    A job that cannot start until a machine is free is not counted as weeks
    of material delay. ``extra_days`` is the supplier's lateness.
    """
    if actual_start <= expected_morning:
        return None
    cap_days = min(2, max(1, int(extra_days)))
    day = utc_to_shop(actual_start).date()
    left = cap_days
    guard = 0
    while left > 0 and guard < 14:
        day -= timedelta(days=1)
        guard += 1
        if shop.is_working(day):
            left -= 1
    floor = shop_local_to_utc(day, time(8, 0))
    previous = expected_morning if expected_morning > floor else floor
    if previous >= actual_start:
        return None
    return previous


def _plant_downtime(rng, shop, ledger, plans, counters):
    """Afternoon breakdowns, closed the same day, sized against the expected material delay."""
    material_jobs = sum(1 for plan in plans if plan["needs"] and plan["fate"] == "run")
    expected = material_jobs * ((0.04 * 8) + (0.18 * 12) + (0.26 * 14)) / 3
    counters["delay_target_material"] = expected
    target = expected * 0.75
    units = [unit for grouped in shop.units_by_type.values() for unit in grouped]
    if not units or not shop.workers or target < 3:
        return
    days = list(shop.working_days)
    rng.shuffle(days)
    reporter = shop.workers[0]
    made = 0.0
    for day in days:
        if made >= target:
            break
        unit = units[rng.randrange(len(units))]
        start = shop_local_to_utc(day, time(13, 0))
        end = shop_local_to_utc(day, time(16, 0))
        category, reason = DOWNTIME_REASONS[rng.randrange(len(DOWNTIME_REASONS))]
        row = MachineDowntime(
            id=_new_id(rng),
            machine_unit_id=unit.id,
            started_at=start,
            ended_at=end,
            category=category,
            reason=reason,
            reported_by_id=reporter.id,
            expected_repair_date=day,
            created_at=start,
        )
        ledger.add(row, start)
        shop.unit_busy[unit.id].append((ensure_utc(start), ensure_utc(end)))
        counters["breakdowns"] += 1
        counters["downtime_hours"] += 3
        made += 3


def _pick_pause(rng):
    reason, _share, _length, note = PAUSE_TARGETS[0]
    return reason, note


def _pick_rework(rng):
    roll = rng.random()
    acc = 0.0
    for category, weight, text in REWORK_WEIGHTS:
        acc += weight
        if roll <= acc:
            return category, text
    return REWORK_WEIGHTS[0][0], REWORK_WEIGHTS[0][2]


def _stock_size(title):
    """Bar stock for a part, written the way the shop writes it on a PO:
    "Drive shaft, 50 mm" needs a 60mm dia x 150mmL piece."""
    raw = title.split(", ", 1)[-1].replace("mm", "").strip()
    try:
        part = int(float(raw))
    except ValueError:
        return None
    return f"{part + 10}mm dia x {part * 3}mmL"


def _usage_rate(name):
    text = (name or "").lower()
    # Oil and other liquids come in pails or gallons and last for weeks.
    if any(word in text for word in ("oil", "coolant", "grease", "fluid")):
        return "bulk"
    if any(word in text for word in ("disc", "disk", "wheel", "cutting", "grinding")):
        return "high"
    if any(word in text for word in ("tap", "insert")):
        return "low"
    return "mid"


def _shift_time(value, minutes):
    base = datetime(2000, 1, 1, value.hour, value.minute) + timedelta(minutes=int(minutes))
    if base.date() != date(2000, 1, 1):
        return time(23, 50)
    return base.time().replace(second=0, microsecond=0)


def _has_hours(schedule):
    return any(
        row and row.is_working and row.start_time and row.end_time for row in (schedule or {}).values()
    )


def _fmt_counts(counts):
    if not counts:
        return "none"
    return ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))


def _freeze_onupdate():
    saved = [(col, col.onupdate) for col in _ONUPDATE_COLUMNS]
    for col, _previous in saved:
        col.onupdate = None
    return saved


def _thaw_onupdate(saved):
    for col, previous in saved:
        col.onupdate = previous


@contextmanager
def _quiet_audit():
    """The global listeners would stamp audit rows with 'now' and leave them
    out of the ledger. Job audits are written explicitly at the event time."""
    hooks = (
        ("after_insert", _after_insert),
        ("before_update", _before_update),
        ("after_delete", _after_delete),
    )
    removed = []
    for model in _AUDIT_MODELS:
        for name, fn in hooks:
            if event.contains(model, name, fn):
                event.remove(model, name, fn)
                removed.append((model, name, fn))
    try:
        yield
    finally:
        for model, name, fn in removed:
            if not event.contains(model, name, fn):
                event.listen(model, name, fn)

