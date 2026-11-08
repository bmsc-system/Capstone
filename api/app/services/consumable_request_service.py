"""Running-low reports for consumables, and what Production Workers may see of
inventory.

Workers report an item as running low or out from the shop floor. The Office
Staff see open reports on their dashboard, in the Consumables tab and in the
bell. A report closes when the item is bought (a recorded purchase or a
received supplier order line), when the Office Staff dismiss it, or when the
worker who reported it takes it back.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.models.consumable_request import (
    ConsumableRequest,
    ConsumableRequestLevel,
    ConsumableRequestStatus,
)
from app.models.staff_alert import StaffAlertKind
from app.models.tool import Tool, ToolCategory
from app.models.user import User, UserRole
from app.utils.errors import AppError
from app.utils.row_locks import lock_row

LEVEL_WORDS = {ConsumableRequestLevel.LOW: "running low", ConsumableRequestLevel.OUT: "out"}


def _now():
    return datetime.now(timezone.utc)


def _consumable(tool_id) -> Tool:
    tool = lock_row(Tool, tool_id) if tool_id else None
    if tool is None or tool.category != ToolCategory.CONSUMABLE or not tool.active:
        raise AppError("That item is not in the consumables list.", "NOT_FOUND", 404)
    return tool


def _open_for(tool_id):
    return ConsumableRequest.query.filter_by(
        tool_id=tool_id, status=ConsumableRequestStatus.OPEN
    ).first()


def open_requests() -> list[ConsumableRequest]:
    return (
        ConsumableRequest.query.filter_by(status=ConsumableRequestStatus.OPEN)
        .order_by(ConsumableRequest.reported_at.desc())
        .all()
    )


def _alert(tool: Tool, req: ConsumableRequest, user: User | None):
    from app.services.staff_alert_service import raise_alert

    who = (user.nickname or user.full_name) if user else "A worker"
    raise_alert(
        roles=[UserRole.OFFICE_STAFF],
        kind=StaffAlertKind.LOW_STOCK,
        title=f"{tool.name} is {LEVEL_WORDS[req.level]}",
        message=f"Reported by {who} from the shop floor.",
        # One bell alert per report and level: tapping again does not ring twice.
        dedupe_key=f"consumable-request:{req.id}:{req.level}",
    )


def report(tool_id: str, level: str, user_id: str) -> ConsumableRequest:
    """Open a report for the item, or update the open one. Repeating the same
    tap changes nothing; "Out" after "Running low" raises the level."""
    level = (level or ConsumableRequestLevel.LOW).upper()
    if level not in ConsumableRequestLevel.ALL:
        raise AppError("Choose Running low or Out.", "VALIDATION_ERROR", 400)
    # Lock the item so two taps at once still leave one open report.
    tool = _consumable(tool_id)
    user = db.session.get(User, user_id)
    req = _open_for(tool.id)
    if req is None:
        req = ConsumableRequest(
            tool_id=tool.id, level=level, status=ConsumableRequestStatus.OPEN, reported_by_id=user_id
        )
        db.session.add(req)
        db.session.flush()
        _alert(tool, req, user)
    elif level == ConsumableRequestLevel.OUT and req.level != ConsumableRequestLevel.OUT:
        req.level = ConsumableRequestLevel.OUT
        req.reported_by_id = user_id
        req.reported_at = _now()
        db.session.flush()
        _alert(tool, req, user)
    db.session.commit()
    return req


def _close(req: ConsumableRequest, status: str, user_id: str | None, **links):
    req.status = status
    req.closed_at = _now()
    req.closed_by_id = user_id
    for key, value in links.items():
        setattr(req, key, value)


def withdraw(request_id: str, user_id: str) -> ConsumableRequest:
    """The worker who reported it takes it back (tapped by mistake)."""
    req = lock_row(ConsumableRequest, request_id)
    if req is None:
        raise AppError("Report not found.", "NOT_FOUND", 404)
    if req.reported_by_id != user_id:
        raise AppError("Only the person who reported it can take it back.", "FORBIDDEN", 403)
    if req.status == ConsumableRequestStatus.OPEN:
        _close(req, ConsumableRequestStatus.WITHDRAWN, user_id)
        db.session.commit()
    return req


def dismiss(request_id: str, user_id: str) -> ConsumableRequest:
    """The Office Staff close a report without buying (enough is on the shelf)."""
    req = lock_row(ConsumableRequest, request_id)
    if req is None:
        raise AppError("Report not found.", "NOT_FOUND", 404)
    if req.status == ConsumableRequestStatus.OPEN:
        _close(req, ConsumableRequestStatus.DISMISSED, user_id)
        db.session.commit()
    return req


def close_on_purchase(tool_id: str, user_id: str | None, *, consumable_purchase_id=None,
                      material_purchase_id=None) -> int:
    """Buying the item answers its open report. Does not commit (the caller saves
    the purchase in the same transaction)."""
    req = _open_for(tool_id)
    if req is None:
        return 0
    _close(
        req,
        ConsumableRequestStatus.BOUGHT,
        user_id,
        consumable_purchase_id=consumable_purchase_id,
        material_purchase_id=material_purchase_id,
    )
    return 1


def worker_consumables(viewer_id: str) -> list[dict]:
    """Offered consumables with any open report: names, sizes and units only.
    No prices, purchase history or forecast (those are office information)."""
    open_by_tool = {r.tool_id: r for r in open_requests()}
    tools = (
        Tool.query.filter(Tool.category == ToolCategory.CONSUMABLE, Tool.active.is_(True))
        .order_by(Tool.name.asc())
        .all()
    )
    rows = []
    for t in tools:
        req = open_by_tool.get(t.id)
        rows.append(
            {
                "toolId": t.id,
                "name": t.name,
                "sizeSpec": t.size_spec,
                "shopTerm": t.shop_term,
                "unit": t.unit,
                "report": req.to_dict(viewer_id) if req else None,
            }
        )
    # Reported items first, then by name.
    rows.sort(key=lambda r: (r["report"] is None, r["name"].lower()))
    return rows


def worker_materials(worker_id: str) -> list[dict]:
    """Raw materials of the jobs the worker still has work on (as lead or
    helper): what, what size, how much, and whether it has arrived. No costs."""
    from app.models.operation import OperationStatus
    from app.services.operation_service import list_my_operations

    jobs = {}
    for op in list_my_operations(worker_id):
        if op.status == OperationStatus.COMPLETED:
            continue
        job = op.job_order
        if job is None or job.id in jobs:
            continue
        lines = [
            {k: v for k, v in line.items() if k not in ("supplierName", "poNumber")}
            for line in job.material_lines_summary()
        ]
        jobs[job.id] = {
            "jobId": job.id,
            "jobNumber": job.job_number,
            "title": job.title,
            "clientName": job.client.name if job.client else None,
            "dueDate": job.due_date.isoformat() if job.due_date else None,
            "materialStatus": job.material_status.value if job.material_status else None,
            "lines": lines,
        }
    return sorted(jobs.values(), key=lambda j: (j["dueDate"] or "9999", j["jobNumber"] or ""))
