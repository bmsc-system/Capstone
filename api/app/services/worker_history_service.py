"""Worker completed-ops and tool activity history (no new tables)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app.models.operation import JobOperation, OperationStatus
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.tool_type import ToolUnit, ToolUnitStatus
from app.services.worker_profile_service import get_worker_or_404

ON_ESTIMATE_BAND = Decimal("10")
SUMMARY_MIN_OPS = 2


def _parse_day(value, *, end=False):
    if not value:
        return None
    raw = str(value).strip()
    if len(raw) == 10:
        raw = f"{raw}T23:59:59.999999Z" if end else f"{raw}T00:00:00Z"
    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _num(v):
    if v is None:
        return None
    return float(v)


def _job_number(job):
    if not job:
        return None
    year = job.created_at.year if job.created_at else datetime.now(timezone.utc).year
    short = (job.id or "")[:4].upper()
    return f"JO-{year}-{short}"


def _actual_hours(op: JobOperation):
    if op.actual_worked_hours is not None:
        return float(op.actual_worked_hours)
    if op.actual_start and op.actual_end:
        return (op.actual_end - op.actual_start).total_seconds() / 3600.0
    return None


def worker_work_history(worker_id, *, from_s=None, to_s=None, page=1, per_page=20):
    get_worker_or_404(worker_id)
    page = max(1, int(page or 1))
    per_page = min(100, max(1, int(per_page or 20)))
    start_utc = _parse_day(from_s, end=False)
    end_utc = _parse_day(to_s, end=True)

    base = JobOperation.query.options(
        joinedload(JobOperation.job_order),
        joinedload(JobOperation.machine_unit),
        joinedload(JobOperation.operation_type),
    ).filter(
        JobOperation.crew_includes(worker_id),
        JobOperation.status == OperationStatus.COMPLETED,
    )
    if start_utc is not None:
        base = base.filter(JobOperation.actual_end >= start_utc)
    if end_utc is not None:
        base = base.filter(JobOperation.actual_end <= end_utc)

    all_completed = base.order_by(JobOperation.actual_end.desc().nullslast()).all()

    primary = [
        op
        for op in all_completed
        if op.rework_of_operation_id is None
        and op.estimated_hours is not None
        and _actual_hours(op) is not None
    ]
    rework_count = sum(1 for op in all_completed if op.rework_of_operation_id)

    op_count = len(primary)
    total_est = sum(float(op.estimated_hours) for op in primary)
    total_act = sum(_actual_hours(op) or 0.0 for op in primary)
    var_vals = [
        float(op.variance_pct)
        for op in primary
        if op.variance_pct is not None
    ]
    on_est = sum(
        1
        for op in primary
        if op.variance_pct is not None
        and -float(ON_ESTIMATE_BAND) <= float(op.variance_pct) <= float(ON_ESTIMATE_BAND)
    )

    enough = op_count >= SUMMARY_MIN_OPS
    summary = {
        # First-time operations with a target and hours: the base for the
        # averages. Redos are left out of the averages and counted separately.
        "operationsCompleted": op_count,
        # Every completed operation in the range (first-time and redo), the
        # same count as the table below.
        "operationsTotal": len(all_completed),
        "reworkCount": rework_count,
        "enoughHistory": enough,
        "minimumForAverages": SUMMARY_MIN_OPS,
        "totalEstimatedHours": round(total_est, 2) if enough else None,
        "totalActualHours": round(total_act, 2) if enough else None,
        "averageVariancePct": round(sum(var_vals) / len(var_vals), 2)
        if enough and var_vals
        else None,
        "onEstimateRatePct": round(on_est / op_count * 100, 1) if enough and op_count else None,
        "message": None
        if enough
        else (
            f"Too few completed operations yet ({op_count}) to show averages — "
            f"need at least {SUMMARY_MIN_OPS}."
        ),
    }

    total = len(all_completed)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    start_i = (page - 1) * per_page
    page_ops = all_completed[start_i : start_i + per_page]

    operations = []
    for op in page_ops:
        est = float(op.estimated_hours) if op.estimated_hours is not None else None
        act = _actual_hours(op)
        diff = None
        if est is not None and act is not None:
            diff = round(act - est, 2)
        operations.append(
            {
                "id": op.id,
                "completedAt": op.actual_end.isoformat() if op.actual_end else None,
                "jobOrderId": op.job_order_id,
                "jobNumber": _job_number(op.job_order),
                "operationName": op.operation_name,
                "operationTypeName": op.operation_type.name if op.operation_type else None,
                "machineUnitLabel": op.machine_unit.label if op.machine_unit else None,
                "estimatedHours": est,
                "actualHours": round(act, 2) if act is not None else None,
                "differenceHours": diff,
                "isRework": bool(op.rework_of_operation_id),
                "crewSize": op.crew_size,
                "isCrew": op.crew_size > 1,
                "crewRole": "LEAD" if op.assigned_worker_id == worker_id else "HELPER",
                "crew": op.crew_dicts(),
            }
        )

    held = (
        ToolUnit.query.filter_by(current_holder_id=worker_id, status=ToolUnitStatus.OUT)
        .order_by(ToolUnit.held_since.desc().nullslast())
        .all()
    )
    tools_held = [u.to_dict() for u in held]

    events = (
        ToolEvent.query.filter_by(worker_id=worker_id)
        .filter(ToolEvent.tool_unit_id.isnot(None))
        .filter(ToolEvent.type.in_([ToolEventType.BORROW, ToolEventType.RETURN]))
        .order_by(ToolEvent.created_at.desc())
        .limit(30)
        .all()
    )
    tool_events = [e.to_dict() for e in events]

    # Consumables handed out to this worker (not undone), newest first.
    issued = (
        ToolEvent.query.filter_by(worker_id=worker_id, type=ToolEventType.ISSUE)
        .filter(ToolEvent.tool_id.isnot(None))
        .filter(ToolEvent.undone_at.is_(None))
        .order_by(ToolEvent.created_at.desc())
        .limit(30)
        .all()
    )
    consumables_used = []
    for e in issued:
        row = e.to_dict()
        row["jobNumber"] = _job_number(e.job_order)
        consumables_used.append(row)

    return {
        "summary": summary,
        "operations": {
            "items": operations,
            "total": total,
            "page": page,
            "pages": pages,
            "perPage": per_page,
        },
        "toolsHeld": tools_held,
        "toolEvents": tool_events,
        "consumablesUsed": consumables_used,
    }
