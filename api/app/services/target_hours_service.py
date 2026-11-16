"""Target hours suggested from finished work, so the Admin does not have to guess.

For each operation type the planner can pick, at most one suggestion:

* Same job before: the latest completed operation of that type on an earlier
  job order of the same client with the same title. Its hours worked are
  used, scaled by quantity when both jobs have one.
* Otherwise the median hours worked of the type over its most recent completed
  operations, once there are at least ``MIN_SAMPLES`` of them.
* Otherwise nothing, and the Admin types the hours.

Only real hours count: rework and work done by an outside shop are left out,
and so are operations without recorded hours worked. The Admin can always
change the number; it is a starting point, not a rule.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from statistics import median

from app.extensions import db
from app.models.job_order import JobOrder
from app.models.operation import JobOperation, OperationStatus
from app.models.worker_skill import OperationType

MIN_SAMPLES = 3
RECENT_LIMIT = 30
STEP = Decimal("0.25")


def _title_key(title: str | None) -> str:
    return " ".join((title or "").lower().split())


def _round(hours: float) -> float:
    """Nearest quarter hour, never below one quarter."""
    q = (Decimal(str(hours)) / STEP).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * STEP
    return float(max(q, STEP))


def _history(exclude_job_id: str):
    """(type id, hours worked, job) of completed in-house work, newest first."""
    return (
        db.session.query(JobOperation.operation_type_id, JobOperation.actual_worked_hours, JobOrder)
        .join(JobOrder, JobOrder.id == JobOperation.job_order_id)
        .join(OperationType, OperationType.id == JobOperation.operation_type_id)
        .filter(
            JobOperation.status == OperationStatus.COMPLETED,
            JobOperation.rework_of_operation_id.is_(None),
            JobOperation.actual_worked_hours.isnot(None),
            JobOperation.actual_worked_hours > 0,
            OperationType.is_outsourced.is_(False),
            JobOperation.job_order_id != exclude_job_id,
        )
        .order_by(JobOperation.actual_end.desc().nullslast())
        .all()
    )


def suggestions_for_job(job: JobOrder) -> dict:
    """{operation type id: {hours, basis, samples, jobNumber?, ...}}."""
    title = _title_key(job.title)
    qty = float(job.quantity) if job.quantity else None
    by_type: dict[str, list[float]] = {}
    same_job: dict[str, dict] = {}
    for type_id, worked, past in _history(job.id):
        hours = float(worked)
        if (
            type_id not in same_job
            and past.client_id == job.client_id
            and _title_key(past.title) == title
        ):
            past_qty = float(past.quantity) if past.quantity else None
            scaled = hours * qty / past_qty if qty and past_qty else hours
            same_job[type_id] = {
                "hours": _round(scaled),
                "basis": "SAME_JOB",
                "samples": 1,
                "jobNumber": past.job_number,
                "pastHours": round(hours, 2),
                "pastQuantity": past_qty,
                "quantity": qty,
            }
        bucket = by_type.setdefault(type_id, [])
        if len(bucket) < RECENT_LIMIT:
            bucket.append(hours)

    out = dict(same_job)
    for type_id, hours in by_type.items():
        if type_id in out or len(hours) < MIN_SAMPLES:
            continue
        out[type_id] = {
            "hours": _round(median(hours)),
            "basis": "PAST_JOBS",
            "samples": len(hours),
        }
    return out
