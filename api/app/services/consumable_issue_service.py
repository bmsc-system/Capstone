"""Consumables handed out by the Office Staff.

The Office Staff keep the consumables and hand them to a worker for the
operation the worker is on. Each hand-out is an ISSUE event: it lowers the
stock on hand and is tied to the worker and, when there is one, the operation
and its job. Workers only see what is available and what was handed to them.
A hand-out entered by mistake is undone, not deleted, so the record stays.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models.job_order import JobOrder, JobOrderStatus
from app.models.operation import JobOperation, OperationStatus
from app.models.tool import Tool, ToolCategory
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.user import User, UserRole, UserStatus
from app.utils.errors import AppError
from app.utils.row_locks import lock_row

OPEN_JOB_STATUSES = (JobOrderStatus.SCHEDULED, JobOrderStatus.IN_PROGRESS)
OPEN_OP_STATUSES = (
    OperationStatus.PENDING,
    OperationStatus.SCHEDULED,
    OperationStatus.IN_PROGRESS,
    OperationStatus.REWORK,
)


def _bad(message):
    return AppError(message, "VALIDATION_ERROR", 400)


def _qty(value) -> float:
    return float(value or 0)


def _consumable(tool_id) -> Tool:
    tool = db.session.get(Tool, tool_id) if tool_id else None
    if tool is None or tool.category != ToolCategory.CONSUMABLE:
        raise _bad("Pick a consumable.")
    return tool


def _worker(worker_id) -> User:
    worker = db.session.get(User, worker_id) if worker_id else None
    if worker is None or worker.role != UserRole.PRODUCTION_WORKER:
        raise _bad("Pick the production worker receiving it.")
    if worker.status == UserStatus.DISABLED:
        raise _bad(f"{worker.full_name} is deactivated.")
    return worker


def _is_running(op: JobOperation) -> bool:
    from app.services.operation_service import _is_running as running

    return running(op)


def open_operations(worker_id: str) -> list[JobOperation]:
    """The worker's unfinished operations on released jobs: the one with the
    clock on first, then paused work, then by schedule."""
    ops = (
        JobOperation.query.join(JobOrder, JobOperation.job_order_id == JobOrder.id)
        .filter(
            JobOperation.crew_includes(worker_id),
            JobOperation.status.in_(OPEN_OP_STATUSES),
            JobOrder.status.in_(OPEN_JOB_STATUSES),
        )
        .all()
    )
    far = datetime.max.replace(tzinfo=timezone.utc)

    def rank(op):
        if _is_running(op):
            return (0, far)
        if op.status == OperationStatus.IN_PROGRESS:
            return (1, far)
        return (2, op.scheduled_start or far)

    return sorted(ops, key=rank)


def _op_option(op: JobOperation) -> dict:
    job = op.job_order
    return {
        "operationId": op.id,
        "operationName": op.operation_name,
        "sequenceNo": op.sequence_no,
        "jobOrderId": op.job_order_id,
        "jobNumber": job.job_number if job else None,
        "jobTitle": job.title if job else None,
        "running": _is_running(op),
        "status": op.status.value,
        "scheduledStart": op.scheduled_start.isoformat() if op.scheduled_start else None,
    }


def issue_options(worker_id: str) -> dict:
    """What the Issue form offers for a worker: their open operations, with the
    one to pick by default (the operation they are on now)."""
    worker = _worker(worker_id)
    ops = open_operations(worker.id)
    current = next((op for op in ops if _is_running(op)), None)
    return {
        "workerId": worker.id,
        "operations": [_op_option(op) for op in ops],
        "currentOperationId": current.id if current else None,
    }


def issue_workers() -> list[dict]:
    """Production workers a consumable can be handed to, with the operation
    each has the clock on now (those working come first)."""
    workers = (
        User.query.filter(
            User.role == UserRole.PRODUCTION_WORKER, User.status != UserStatus.DISABLED
        )
        .order_by(User.full_name.asc())
        .all()
    )
    running = {}
    ops = JobOperation.query.filter(JobOperation.status == OperationStatus.IN_PROGRESS).all()
    for op in ops:
        if not _is_running(op):
            continue
        for wid in op.crew_ids:
            running.setdefault(wid, op)
    rows = []
    for w in workers:
        op = running.get(w.id)
        job = op.job_order if op else None
        rows.append(
            {
                "workerId": w.id,
                "name": w.full_name,
                "currentOperation": (
                    f"{op.operation_name or 'Operation'} ({job.job_number})" if op and job else None
                ),
            }
        )
    rows.sort(key=lambda r: (r["currentOperation"] is None, r["name"].lower()))
    return rows


def _decimal(value) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise _bad("Enter the quantity handed out.")
    if not number.is_finite() or number <= 0:
        raise _bad("Enter the quantity handed out.")
    return number


def event_row(event: ToolEvent) -> dict:
    tool, op = event.tool, event.operation
    job = event.job_order
    return {
        "id": event.id,
        "toolId": event.tool_id,
        "name": tool.name if tool else None,
        "sizeSpec": tool.size_spec if tool else None,
        "unit": tool.unit if tool else None,
        "quantity": _qty(event.quantity),
        "workerId": event.worker_id,
        "workerName": event.worker.full_name if event.worker else None,
        "operationId": event.operation_id,
        "operationName": op.operation_name if op else None,
        "jobOrderId": event.job_order_id,
        "jobNumber": job.job_number if job else None,
        "issuedByName": event.recorded_by.full_name if event.recorded_by else None,
        "issuedAt": event.created_at.isoformat() if event.created_at else None,
        "undoneAt": event.undone_at.isoformat() if event.undone_at else None,
    }


def issue(data: dict, actor_id: str) -> dict:
    """Hand ``quantity`` of a consumable to a worker. With no operation given,
    it goes on the operation the worker has the clock on, when there is one."""
    tool = _consumable(data.get("toolId"))
    worker = _worker(data.get("workerId"))
    quantity = _decimal(data.get("quantity"))
    op = None
    if "operationId" in data:
        if data.get("operationId"):
            op = db.session.get(JobOperation, data["operationId"])
            if op is None or worker.id not in op.crew_ids:
                raise _bad(f"{worker.full_name} is not on that operation.")
            if op.status == OperationStatus.COMPLETED:
                raise _bad("That operation is already completed.")
    else:
        op = next((o for o in open_operations(worker.id) if _is_running(o)), None)

    # Two hand-outs of one item at once: each waits so no deduction is lost.
    tool = lock_row(Tool, tool.id)
    on_hand = Decimal(str(tool.quantity_on_hand or 0))
    if quantity > on_hand:
        raise AppError(
            f"Only {_qty(on_hand):g} {tool.unit} of {tool.name} on hand.",
            "INSUFFICIENT_STOCK",
            409,
        )
    tool.quantity_on_hand = on_hand - quantity
    event = ToolEvent(
        tool_id=tool.id,
        worker_id=worker.id,
        type=ToolEventType.ISSUE,
        quantity=quantity,
        stock_delta=-quantity,
        job_order_id=op.job_order_id if op else None,
        operation_id=op.id if op else None,
        recorded_by_id=actor_id,
        reason=(str(data.get("note") or "").strip() or None),
    )
    db.session.add(event)
    db.session.commit()
    row = event_row(event)
    row["onHandAfter"] = _qty(tool.quantity_on_hand)
    return row


def undo_issue(event_id: str, actor_id: str) -> dict:
    """Take back a hand-out entered by mistake: the quantity returns to stock."""
    event = db.session.get(ToolEvent, event_id)
    if event is None or event.type != ToolEventType.ISSUE or not event.tool_id:
        raise AppError("Hand-out not found", "NOT_FOUND", 404)
    if event.undone_at is not None:
        raise _bad("This hand-out was already undone.")
    tool = lock_row(Tool, event.tool_id)
    qty = Decimal(str(event.quantity or 0))
    tool.quantity_on_hand = Decimal(str(tool.quantity_on_hand or 0)) + qty
    event.undone_at = datetime.now(timezone.utc)
    db.session.add(
        ToolEvent(
            tool_id=tool.id,
            worker_id=event.worker_id,
            type=ToolEventType.UNDO,
            quantity=qty,
            stock_delta=qty,
            job_order_id=event.job_order_id,
            operation_id=event.operation_id,
            recorded_by_id=actor_id,
            undoes_event_id=event.id,
        )
    )
    db.session.commit()
    row = event_row(event)
    row["onHandAfter"] = _qty(tool.quantity_on_hand)
    return row


def _issues():
    return ToolEvent.query.filter(
        ToolEvent.type == ToolEventType.ISSUE, ToolEvent.tool_id.isnot(None)
    )


def list_issues(*, tool_id=None, worker_id=None, job_order_id=None, limit=100) -> list[dict]:
    """Hand-outs, newest first (undone ones included, marked)."""
    q = _issues()
    if tool_id:
        q = q.filter(ToolEvent.tool_id == tool_id)
    if worker_id:
        q = q.filter(ToolEvent.worker_id == worker_id)
    if job_order_id:
        q = q.filter(ToolEvent.job_order_id == job_order_id)
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100
    return [event_row(e) for e in q.order_by(ToolEvent.created_at.desc()).limit(limit).all()]


def worker_view(worker_id: str) -> dict:
    """For a worker: every offered consumable with how much is available, and
    what was handed to them. No prices, purchase history or forecast."""
    tools = (
        Tool.query.filter(Tool.category == ToolCategory.CONSUMABLE, Tool.active.is_(True))
        .order_by(Tool.name.asc())
        .all()
    )
    used = (
        _issues()
        .filter(ToolEvent.worker_id == worker_id, ToolEvent.undone_at.is_(None))
        .order_by(ToolEvent.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "items": [
            {
                "toolId": t.id,
                "name": t.name,
                "sizeSpec": t.size_spec,
                "shopTerm": t.shop_term,
                "unit": t.unit,
                "onHand": _qty(t.quantity_on_hand),
            }
            for t in tools
        ],
        "used": [event_row(e) for e in used],
    }


def job_issues(job_order_id: str) -> list[dict]:
    """Consumables handed out for a job's operations (not undone), latest first."""
    events = (
        _issues()
        .filter(ToolEvent.job_order_id == job_order_id, ToolEvent.undone_at.is_(None))
        .order_by(ToolEvent.created_at.desc())
        .all()
    )
    return [event_row(e) for e in events]
