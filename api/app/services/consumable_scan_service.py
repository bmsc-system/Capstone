"""Worker consumable scans (−1 unit), debounce, undo, and unified QR scan."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.extensions import db
from app.models.operation import JobOperation, OperationStatus
from app.models.tool import Tool, ToolCategory
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.tool_type import ToolUnit
from app.utils.errors import AppError
from app.utils.row_locks import lock_row

SCAN_DEBOUNCE_SECONDS = 10
UNDO_WINDOW_MINUTES = 5


def _utcnow():
    return datetime.now(timezone.utc)


def _dec(v) -> Decimal:
    return Decimal(str(v or 0))


def worker_in_progress_operation(worker_id: str) -> JobOperation | None:
    """First IN_PROGRESS operation where the worker is lead or helper."""
    from app.models.operation import OperationHelper

    lead = (
        JobOperation.query.filter_by(
            assigned_worker_id=worker_id, status=OperationStatus.IN_PROGRESS
        )
        .order_by(JobOperation.actual_start.desc())
        .first()
    )
    if lead:
        return lead
    helper = (
        OperationHelper.query.join(
            JobOperation, OperationHelper.operation_id == JobOperation.id
        )
        .filter(
            OperationHelper.worker_id == worker_id,
            JobOperation.status == OperationStatus.IN_PROGRESS,
        )
        .first()
    )
    return helper.operation if helper else None


def _recent_scan(tool_id: str, worker_id: str, within_seconds: int) -> ToolEvent | None:
    since = _utcnow() - timedelta(seconds=within_seconds)
    return (
        ToolEvent.query.filter(
            ToolEvent.tool_id == tool_id,
            ToolEvent.worker_id == worker_id,
            ToolEvent.type == ToolEventType.SCAN,
            ToolEvent.undone_at.is_(None),
            ToolEvent.created_at >= since,
        )
        .order_by(ToolEvent.created_at.desc())
        .first()
    )


def _scan_payload(event: ToolEvent, *, needs_confirm: bool = False) -> dict:
    tool = event.tool
    data = event.to_dict()
    data["kind"] = "CONSUMABLE_SCAN"
    data["needsConfirm"] = needs_confirm
    data["confirmMessage"] = (
        f"You just scanned {tool.name}. Scan again?" if needs_confirm and tool else None
    )
    data["displayLine"] = (
        f"{tool.name} −1 {tool.unit}" if tool else None
    )
    data["canUndoUntil"] = (
        (event.created_at + timedelta(minutes=UNDO_WINDOW_MINUTES)).isoformat()
        if event.created_at
        else None
    )
    return data


def scan_consumable(
    tool: Tool,
    worker_id: str,
    *,
    client_action_id: str | None = None,
    confirm_repeat: bool = False,
    commit: bool = True,
) -> dict:
    """Deduct one unit. Debounce: same worker+item within 10s needs confirm_repeat."""
    if tool.category != ToolCategory.CONSUMABLE:
        raise AppError("Only consumables can be scanned for usage", "VALIDATION_ERROR", 400)
    # Simultaneous scans of one item: each waits its turn so no deduction is lost.
    tool = lock_row(Tool, tool.id)

    if client_action_id:
        existing = ToolEvent.query.filter_by(client_action_id=client_action_id).first()
        if existing:
            return _scan_payload(existing)

    recent = _recent_scan(tool.id, worker_id, SCAN_DEBOUNCE_SECONDS)
    if recent and not confirm_repeat:
        return _scan_payload(recent, needs_confirm=True)

    if _dec(tool.quantity_on_hand) < 1:
        raise AppError(
            f"No {tool.unit} of {tool.name} left on hand",
            "INSUFFICIENT_STOCK",
            409,
        )

    op = worker_in_progress_operation(worker_id)
    tool.quantity_on_hand = _dec(tool.quantity_on_hand) - Decimal("1")
    event = ToolEvent(
        tool_id=tool.id,
        worker_id=worker_id,
        type=ToolEventType.SCAN,
        quantity=Decimal("1"),
        stock_delta=Decimal("-1"),
        job_order_id=op.job_order_id if op else None,
        operation_id=op.id if op else None,
        client_action_id=(client_action_id or None),
    )
    db.session.add(event)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return _scan_payload(event)


def undo_consumable_scan(event_id: str, worker_id: str) -> dict:
    event = lock_row(ToolEvent, event_id)
    if not event or event.type != ToolEventType.SCAN:
        raise AppError("Scan not found", "NOT_FOUND", 404)
    if event.worker_id != worker_id:
        raise AppError("Only the worker who scanned can undo", "FORBIDDEN", 403)
    if event.undone_at is not None:
        raise AppError("This scan was already undone", "CONFLICT", 409)
    deadline = event.created_at + timedelta(minutes=UNDO_WINDOW_MINUTES)
    if event.created_at.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if _utcnow() > deadline:
        raise AppError(
            f"Undo window is {UNDO_WINDOW_MINUTES} minutes",
            "UNDO_EXPIRED",
            409,
        )
    tool = lock_row(Tool, event.tool_id)
    if not tool:
        raise AppError("Item not found", "NOT_FOUND", 404)

    now = _utcnow()
    event.undone_at = now
    tool.quantity_on_hand = _dec(tool.quantity_on_hand) + Decimal("1")
    undo = ToolEvent(
        tool_id=tool.id,
        worker_id=worker_id,
        type=ToolEventType.UNDO,
        quantity=Decimal("1"),
        stock_delta=Decimal("1"),
        job_order_id=event.job_order_id,
        operation_id=event.operation_id,
        undoes_event_id=event.id,
        reason="Undo last scan",
    )
    db.session.add(undo)
    db.session.commit()
    data = undo.to_dict()
    data["kind"] = "CONSUMABLE_UNDO"
    data["displayLine"] = f"Undo: {tool.name} +1 {tool.unit}"
    data["undoneScanId"] = event.id
    return data


def unified_scan(
    code: str,
    worker_id: str,
    *,
    intent: str | None = None,
    client_action_id: str | None = None,
    confirm_repeat: bool = False,
) -> dict:
    """One scan entry: a tool unit is borrowed or returned.

    Consumables are no longer scanned (the shop tracks what it buys instead,
    see ``consumable_purchase_service``); their old QR codes are refused.
    """
    from app.services import tool_event_service as te_service

    raw = (code or "").strip()
    if not raw:
        raise AppError("Scan code is required", "VALIDATION_ERROR", 400)

    unit = ToolUnit.query.filter_by(asset_code=raw).first()
    if unit:
        event = te_service.scan_tool(raw, worker_id, intent=intent)
        data = event.to_dict()
        data["kind"] = "TOOL_UNIT"
        name = unit.tool_type.name if unit.tool_type else "Tool"
        verb = "Returned" if event.type.value == "RETURN" else "Borrowed"
        data["displayLine"] = f"{verb}: {name} ({unit.asset_code})"
        return data

    tool = Tool.query.filter_by(code=raw).first()
    if tool and tool.category == ToolCategory.CONSUMABLE:
        raise AppError(
            f"{tool.name} is a consumable. Consumables are not scanned; only tools are.",
            "CONSUMABLE_NOT_SCANNED",
            400,
        )

    raise AppError("No tool matches that code", "NOT_FOUND", 404)
