"""Interval-level delay facts shared by the Delays analytics: pause time,
material delay (MATERIAL schedule moves only), late returns of outsourced work
and late job orders."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.job_order import JobOrder
from app.models.operation import JobOperation
from app.models.operation_time import (
    MachineDowntime,
    OperationPauseReason,
    OperationTimeEvent,
    OperationTimeLog,
    pause_reason_label,
)
from app.models.schedule_move import DelayKind, MaterialCause, ScheduleMove
from app.services.schedule_calendar import (
    SHOP_TZ,
    ensure_utc,
    intersect_intervals,
    merge_intervals,
    shop_now,
    shop_working_hours,
)

NON_WORKING = frozenset({OperationPauseReason.BREAK.value, OperationPauseReason.END_OF_SHIFT.value})


def _hours(intervals) -> float:
    return sum((e - s).total_seconds() for s, e in intervals) / 3600.0


def _now_utc() -> datetime:
    return shop_now().astimezone(timezone.utc)


def pause_intervals(start_utc, end_utc, job_ids=None) -> list[dict]:
    """Every pause clipped to [start, end), whether or not the operation has
    finished. A pause still open runs until now."""
    now = _now_utc()
    paused_ops = db.session.query(OperationTimeLog.operation_id).filter(
        OperationTimeLog.event == OperationTimeEvent.PAUSE,
        OperationTimeLog.event_at < end_utc,
    )
    q = (
        db.session.query(OperationTimeLog, JobOperation)
        .join(JobOperation, JobOperation.id == OperationTimeLog.operation_id)
        .filter(
            OperationTimeLog.operation_id.in_(paused_ops),
            db.or_(JobOperation.actual_end.is_(None), JobOperation.actual_end > start_utc),
        )
    )
    if job_ids is not None:
        q = q.filter(JobOperation.job_order_id.in_(list(job_ids)))
    rows = q.order_by(OperationTimeLog.operation_id, OperationTimeLog.event_at).all()

    by_op = defaultdict(list)
    ops = {}
    for log, op in rows:
        by_op[op.id].append(log)
        ops[op.id] = op

    out = []
    for op_id, logs in by_op.items():
        op = ops[op_id]
        for i, log in enumerate(logs):
            if log.event != OperationTimeEvent.PAUSE or not log.reason:
                continue
            end_at = next(
                (
                    l.event_at
                    for l in logs[i + 1 :]
                    if l.event in (OperationTimeEvent.RESUME, OperationTimeEvent.COMPLETE)
                ),
                None,
            )
            if end_at is None:
                end_at = ensure_utc(op.actual_end) if op.actual_end else now
            s = max(ensure_utc(log.event_at), ensure_utc(start_utc))
            e = min(ensure_utc(end_at), ensure_utc(end_utc))
            if e <= s:
                continue
            out.append(
                {
                    "operationId": op.id,
                    "jobOrderId": op.job_order_id,
                    "machineUnitId": op.machine_unit_id,
                    "reason": log.reason.value,
                    "start": s,
                    "end": e,
                    "hours": (e - s).total_seconds() / 3600.0,
                }
            )
    return out


def _first_start(job) -> tuple[datetime | None, bool]:
    """(start, started): the first operation's actual start, else its planned start."""
    ops = [o for o in job.operations if o.rework_of_operation_id is None]
    actual = [ensure_utc(o.actual_start) for o in ops if o.actual_start]
    if actual:
        return min(actual), True
    planned = [ensure_utc(o.scheduled_start) for o in ops if o.scheduled_start]
    return (min(planned) if planned else None), False


MATERIAL_CAUSE_LABEL = {
    MaterialCause.SUPPLIER_LATE: "Material delay: supplier late",
    MaterialCause.NOT_ORDERED: "Material delay: not ordered",
}
PARETO_CAUSE = {
    MaterialCause.SUPPLIER_LATE: "MATERIAL_DELAY_SUPPLIER_LATE",
    MaterialCause.NOT_ORDERED: "MATERIAL_DELAY_NOT_ORDERED",
}


def material_delays(start_utc=None, end_utc=None, job_ids=None) -> list[dict]:
    """Time already lost to MATERIAL moves, one row per job and cause.

    The span runs from the planned start before the job's first MATERIAL move
    to the first operation's actual start, or to now while it has not started.
    Each move owns the time from its previous start to the next move's previous
    start (the last one to the end of the span), under that move's cause.
    RESCHEDULED moves never count. With a period, only time inside it counts.
    """
    q = ScheduleMove.query.options(
        joinedload(ScheduleMove.supplier), joinedload(ScheduleMove.supplier_order)
    ).filter(ScheduleMove.kind == DelayKind.MATERIAL)
    if job_ids is not None:
        q = q.filter(ScheduleMove.job_order_id.in_(list(job_ids)))
    moves = defaultdict(list)
    for m in q.order_by(ScheduleMove.moved_at).all():
        moves[m.job_order_id].append(m)
    if not moves:
        return []

    jobs = {
        j.id: j
        for j in JobOrder.query.options(joinedload(JobOrder.operations))
        .filter(JobOrder.id.in_(list(moves.keys())))
        .all()
    }
    now = _now_utc()
    out = []
    for job_id, job_moves in moves.items():
        job = jobs.get(job_id)
        if job is None:
            continue
        original = min(ensure_utc(m.previous_start) for m in job_moves)
        first, started = _first_start(job)
        if first is None:
            continue
        end = min(first, now)
        if end <= original:
            continue

        bounds = []
        for m in job_moves:
            b = max(ensure_utc(m.previous_start), bounds[-1] if bounds else original)
            bounds.append(min(b, end))
        bounds.append(end)
        spans = defaultdict(list)
        cause_moves = defaultdict(list)
        for i, m in enumerate(job_moves):
            cause = m.material_cause or MaterialCause.NOT_ORDERED
            cause_moves[cause].append(m)
            s, e = bounds[i], bounds[i + 1]
            if start_utc is not None:
                s = max(s, ensure_utc(start_utc))
            if end_utc is not None:
                e = min(e, ensure_utc(end_utc))
            if e > s:
                spans[cause].append((s, e))

        for cause, ivs in spans.items():
            hours = shop_working_hours(ivs)
            if hours <= 0:
                continue
            suppliers = []
            if cause == MaterialCause.SUPPLIER_LATE:
                for m in reversed(cause_moves[cause]):
                    name = m.supplier.name if m.supplier else None
                    po = m.supplier_order.po_number if m.supplier_order else None
                    if name and all(x["supplierName"] != name for x in suppliers):
                        suppliers.append({"supplierId": m.supplier_id, "supplierName": name, "poNumber": po})
            out.append(
                {
                    "jobOrderId": job.id,
                    "jobNumber": job.job_number,
                    "cause": cause,
                    "causeLabel": MATERIAL_CAUSE_LABEL[cause],
                    "originalStart": original.isoformat(),
                    "firstStart": first.isoformat(),
                    "started": started,
                    "countedUntil": end.isoformat(),
                    "hours": hours,
                    "moveCount": len(cause_moves[cause]),
                    "suppliers": suppliers,
                    "supplierNames": ", ".join(x["supplierName"] for x in suppliers) or None,
                    "reason": cause_moves[cause][-1].reason,
                }
            )
    out.sort(key=lambda r: -r["hours"])
    return out


OUTSOURCED_CAUSE = "OUTSOURCED_DELAY"
OUTSOURCED_CAUSE_LABEL = "Outsourced delay"


def outsourced_delays(start_utc=None, end_utc=None, job_ids=None) -> list[dict]:
    """Outsourced operations back later than their turnaround, one row each.

    The late time runs from the expected return (sent out + turnaround days) to
    the return, or to now while it is still out, in shop working hours. With a
    period, only time inside it counts.
    """
    from app.models.worker_skill import OperationType

    q = JobOperation.query.options(joinedload(JobOperation.job_order)).filter(
        JobOperation.operation_type.has(OperationType.is_outsourced.is_(True)),
        JobOperation.actual_start.isnot(None),
        JobOperation.turnaround_days.isnot(None),
    )
    if job_ids is not None:
        q = q.filter(JobOperation.job_order_id.in_(list(job_ids)))
    now = _now_utc()
    out = []
    for op in q.all():
        due_back = ensure_utc(op.actual_start) + timedelta(days=int(op.turnaround_days))
        back = ensure_utc(op.actual_end) if op.actual_end else now
        s, e = due_back, back
        if start_utc is not None:
            s = max(s, ensure_utc(start_utc))
        if end_utc is not None:
            e = min(e, ensure_utc(end_utc))
        if e <= s:
            continue
        hours = shop_working_hours([(s, e)])
        if hours <= 0:
            continue
        out.append(
            {
                "operationId": op.id,
                "jobOrderId": op.job_order_id,
                "jobNumber": op.job_order.job_number if op.job_order else None,
                "operationName": op.operation_name,
                "sentTo": op.sent_to,
                "expectedReturn": due_back.isoformat(),
                "returned": op.actual_end is not None,
                "hours": hours,
            }
        )
    out.sort(key=lambda r: -r["hours"])
    return out


def _shop_date(dt) -> date | None:
    return ensure_utc(dt).astimezone(SHOP_TZ).date() if dt else None


def ran_over_target(operations) -> tuple[float, int]:
    """(hours, operations): worked hours beyond target hours on the job's own
    operations. Redo operations from older records are left out."""
    hours = 0.0
    count = 0
    for o in operations:
        if o.rework_of_operation_id is not None or o.estimated_hours is None:
            continue
        if o.is_outsourced:
            continue
        over = float(o.actual_worked_hours or 0) - float(o.estimated_hours)
        if over > 0:
            hours += over
            count += 1
    return hours, count


def late_jobs(period_from: date, period_to: date) -> list[dict]:
    """Job orders delivered in the period after their required date, with the
    delay causes recorded against each (whole job, not clipped to the period)."""
    candidates = (
        JobOrder.query.options(joinedload(JobOrder.operations))
        .filter(JobOrder.delivered_at.isnot(None))
        .all()
    )
    jobs = []
    for job in candidates:
        delivered = _shop_date(job.delivered_at)
        if delivered and period_from <= delivered <= period_to and delivered > job.due_date:
            jobs.append((job, delivered))
    if not jobs:
        return []
    ids = [j.id for j, _ in jobs]

    material = defaultdict(list)
    for r in material_delays(job_ids=ids):
        material[r["jobOrderId"]].append(r)

    far_past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    pauses_by_job = defaultdict(list)
    for p in pause_intervals(far_past, _now_utc(), job_ids=ids):
        if p["reason"] not in NON_WORKING:
            pauses_by_job[p["jobOrderId"]].append(p)

    outsourced = defaultdict(list)
    for r in outsourced_delays(job_ids=ids):
        outsourced[r["jobOrderId"]].append(r)

    downtime_by_job = defaultdict(lambda: defaultdict(list))
    now = _now_utc()
    for d in MachineDowntime.query.filter(MachineDowntime.job_order_id.in_(ids)).all():
        e = ensure_utc(d.ended_at) if d.ended_at else now
        s = ensure_utc(d.started_at)
        if e > s:
            downtime_by_job[d.job_order_id][d.machine_unit_id].append((s, e))

    out = []
    for job, delivered in jobs:
        causes = []
        for mat in material.get(job.id, []):
            causes.append(
                {
                    "cause": PARETO_CAUSE[mat["cause"]],
                    "label": mat["causeLabel"],
                    "hours": mat["hours"],
                    "detail": mat["supplierNames"],
                }
            )

        late_back = outsourced.get(job.id, [])
        if late_back:
            causes.append(
                {
                    "cause": OUTSOURCED_CAUSE,
                    "label": OUTSOURCED_CAUSE_LABEL,
                    "hours": sum(r["hours"] for r in late_back),
                    "detail": ", ".join(
                        sorted({r["sentTo"] for r in late_back if r["sentTo"]})
                    ) or None,
                }
            )

        down_pause = defaultdict(list)
        other_pause = defaultdict(float)
        for p in pauses_by_job.get(job.id, []):
            if p["reason"] == OperationPauseReason.MACHINE_DOWN.value:
                down_pause[p["machineUnitId"]].append((p["start"], p["end"]))
            else:
                other_pause[p["reason"]] += p["hours"]
        units = set(down_pause) | set(downtime_by_job.get(job.id, {}))
        breakdown = 0.0
        for uid in units:
            dt = downtime_by_job.get(job.id, {}).get(uid, [])
            pa = down_pause.get(uid, [])
            breakdown += shop_working_hours(dt) + _hours(pa) - _hours(intersect_intervals(pa, dt))
        if breakdown > 0:
            causes.append({"cause": "BREAKDOWN", "label": "Breakdowns", "hours": breakdown, "detail": None})
        for reason, hrs in sorted(other_pause.items(), key=lambda x: -x[1]):
            causes.append(
                {"cause": reason, "label": pause_reason_label(reason), "hours": hrs, "detail": None}
            )


        over_hours, over_ops = ran_over_target(job.operations)
        if over_hours > 0:
            causes.append(
                {
                    "cause": "RAN_OVER_TARGET",
                    "label": "Ran over target",
                    "hours": over_hours,
                    "detail": f"{over_ops} operation{'s' if over_ops != 1 else ''} over target hours",
                }
            )

        causes.sort(key=lambda c: -c["hours"])
        for c in causes:
            c["hours"] = round(c["hours"], 2)
        out.append(
            {
                "jobOrderId": job.id,
                "jobNumber": job.job_number,
                "clientName": job.client.name if getattr(job, "client", None) else None,
                "dueDate": job.due_date.isoformat(),
                "deliveredDate": delivered.isoformat(),
                "daysLate": (delivered - job.due_date).days,
                "causes": causes,
            }
        )
    out.sort(key=lambda r: (-r["daysLate"], r["jobNumber"]))
    return out
