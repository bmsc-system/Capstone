"""Read-only production efficiency analytics aggregations."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import case, func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.job_order import JobOrder, JobOrderStatus
from app.models.machine import MachineType, MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.models.operation_time import (
    MachineDowntime,
    OperationPauseReason,
    OperationTimeEvent,
    OperationTimeLog,
    pause_reason_label,
)
from app.models.user import User
from app.models.worker_skill import OperationType
from app.services.delay_analysis_service import (
    MATERIAL_CAUSE_LABEL,
    OUTSOURCED_CAUSE,
    OUTSOURCED_CAUSE_LABEL,
    PARETO_CAUSE,
    late_jobs,
    material_delays,
    outsourced_delays,
    pause_intervals,
)
from app.services import forecast_service, scoring_service
from app.services.schedule_calendar import (
    SHOP_TZ,
    derive_working_segments,
    ensure_utc,
    intersect_intervals,
    shop_working_hours,
    load_calendar_exceptions,
    load_crew_schedule_map,
    shop_available_hours,
    shop_local_to_utc,
    shop_now,
)
from app.utils.errors import AppError


DEFAULT_MIN_OPS = 5
ON_ESTIMATE_BAND = Decimal("10")


def _num(v, places=4):
    if v is None:
        return None
    return round(float(v), places)


def _parse_period(from_s, to_s):
    today = shop_now().date()
    if to_s:
        try:
            period_to = date.fromisoformat(to_s)
        except ValueError:
            raise AppError("Invalid 'to' date (YYYY-MM-DD)", "VALIDATION_ERROR", 400)
    else:
        period_to = today
    if from_s:
        try:
            period_from = date.fromisoformat(from_s)
        except ValueError:
            raise AppError("Invalid 'from' date (YYYY-MM-DD)", "VALIDATION_ERROR", 400)
    else:
        period_from = period_to - timedelta(weeks=8)
    if period_from > period_to:
        raise AppError("'from' must be on or before 'to'", "VALIDATION_ERROR", 400)
    start_utc = shop_local_to_utc(period_from, time(0, 0))
    end_utc = shop_local_to_utc(period_to + timedelta(days=1), time(0, 0))
    return period_from, period_to, start_utc, end_utc


def _period_meta(period_from, period_to, excluded):
    return {
        "period": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "excludedOperationCount": int(excluded),
    }


def not_outsourced_filter():
    """Outsourced work has no shop worker or machine, so it stays out of
    efficiency and utilization."""
    return ~JobOperation.operation_type.has(OperationType.is_outsourced.is_(True))


def _completed_in_period_filters(start_utc, end_utc):
    return [
        JobOperation.status == OperationStatus.COMPLETED,
        JobOperation.actual_end.isnot(None),
        JobOperation.actual_end >= start_utc,
        JobOperation.actual_end < end_utc,
        not_outsourced_filter(),
    ]


def count_excluded_null_estimate(start_utc, end_utc):
    """Completed ops in period with null estimated_hours (excluded from variance)."""
    return (
        db.session.query(func.count(JobOperation.id))
        .filter(
            *_completed_in_period_filters(start_utc, end_utc),
            JobOperation.estimated_hours.is_(None),
        )
        .scalar()
        or 0
    )


def _finished_jobs(start_utc, end_utc):
    """(job, completed_at): machining finished in [start, end), completed or delivered."""
    job_complete_subq = (
        db.session.query(
            JobOperation.job_order_id.label("jid"),
            func.max(JobOperation.actual_end).label("completed_at"),
        )
        .filter(JobOperation.status == OperationStatus.COMPLETED)
        .group_by(JobOperation.job_order_id)
        .subquery()
    )
    return (
        db.session.query(JobOrder, job_complete_subq.c.completed_at)
        .join(job_complete_subq, JobOrder.id == job_complete_subq.c.jid)
        .filter(
            JobOrder.status.in_(
                (JobOrderStatus.COMPLETED, JobOrderStatus.FOR_DELIVERY, JobOrderStatus.DELIVERED)
            ),
            job_complete_subq.c.completed_at >= start_utc,
            job_complete_subq.c.completed_at < end_utc,
        )
        .all()
    )


def shop_week_start(dt) -> date:
    """Monday of the shop-local (Manila) week containing ``dt``."""
    local = ensure_utc(dt).astimezone(SHOP_TZ).date()
    return local - timedelta(days=local.weekday())


def _finished_jobs_section(start_utc, end_utc) -> dict:
    """Jobs whose machining finished in the period, and how their delivery went."""
    finished_jobs = _finished_jobs(start_utc, end_utc)
    on_time = awaiting_delivery = 0
    days_late = []
    for job, _completed_at in finished_jobs:
        if not job.delivered_at:
            awaiting_delivery += 1
            continue
        done = job.delivered_at.astimezone(SHOP_TZ).date()
        if done <= job.due_date:
            on_time += 1
        else:
            days_late.append((done - job.due_date).days)
    late = len(days_late)
    return {
        "completed": len(finished_jobs),
        "onTime": on_time,
        "late": late,
        "awaitingDelivery": awaiting_delivery,
        "averageDaysLate": _num(sum(days_late) / late if late else None, 1),
        "maxDaysLate": max(days_late) if days_late else None,
    }


def job_orders_summary(from_s=None, to_s=None):
    """Office view of job orders and deliveries: received, finished, delivered, open now."""
    period_from, period_to, start_utc, end_utc = _parse_period(from_s, to_s)
    today = shop_now().date()

    received = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for job in JobOrder.query.filter(JobOrder.status != JobOrderStatus.CANCELLED).all():
        on = forecast_service._job_received_date(job)
        if period_from <= on <= period_to:
            for key in (job.job_type.value if job.job_type else "OTHER", "ALL"):
                received[key]["count"] += 1
                received[key]["amount"] += float(job.amount or 0)

    delivered = JobOrder.query.filter(
        JobOrder.delivered_at.isnot(None),
        JobOrder.delivered_at >= start_utc,
        JobOrder.delivered_at < end_utc,
    ).all()
    delivered_on_time = sum(
        1 for j in delivered if j.delivered_at.astimezone(SHOP_TZ).date() <= j.due_date
    )

    open_statuses = (
        JobOrderStatus.DRAFT,
        JobOrderStatus.SCHEDULED,
        JobOrderStatus.IN_PROGRESS,
        JobOrderStatus.COMPLETED,
        JobOrderStatus.FOR_DELIVERY,
    )
    open_jobs = JobOrder.query.filter(
        JobOrder.status.in_(open_statuses),
        JobOrder.status != JobOrderStatus.CANCELLED,
    ).all()
    open_by_status = {s.value: 0 for s in open_statuses}
    for j in open_jobs:
        open_by_status[j.status.value] += 1

    payload = _period_meta(period_from, period_to, 0)
    payload.update(
        {
            "received": {
                "count": received["ALL"]["count"],
                "amount": _num(received["ALL"]["amount"], 2),
                "byJobType": [
                    {"jobType": t, "count": v["count"], "amount": _num(v["amount"], 2)}
                    for t, v in sorted(received.items())
                    if t != "ALL"
                ],
            },
            "finished": _finished_jobs_section(start_utc, end_utc),
            "delivered": {
                "count": len(delivered),
                "onTime": delivered_on_time,
                "late": len(delivered) - delivered_on_time,
                "amount": _num(sum(float(j.amount or 0) for j in delivered), 2),
            },
            "openNow": {
                "byStatus": open_by_status,
                "pastDateRequired": sum(
                    1 for j in open_jobs if j.due_date and j.due_date < today
                ),
            },
        }
    )
    return payload


def _worker_period_figures(worker_id, period_from: date, period_to: date) -> dict:
    start_utc = shop_local_to_utc(period_from, time(0, 0))
    end_utc = shop_local_to_utc(period_to + timedelta(days=1), time(0, 0))
    filters = [
        *_completed_in_period_filters(start_utc, end_utc),
        JobOperation.crew_includes(worker_id),
    ]
    finished, redo, worked = (
        db.session.query(
            func.count(JobOperation.id),
            func.count(JobOperation.rework_of_operation_id),
            func.coalesce(func.sum(JobOperation.actual_worked_hours), 0),
        )
        .filter(*filters)
        .one()
    )
    target, worked_on_target = (
        db.session.query(
            func.sum(JobOperation.estimated_hours),
            func.sum(JobOperation.actual_worked_hours),
        )
        .filter(
            *filters,
            JobOperation.rework_of_operation_id.is_(None),
            JobOperation.estimated_hours.isnot(None),
            JobOperation.actual_worked_hours.isnot(None),
        )
        .one()
    )
    return {
        "from": period_from.isoformat(),
        "to": period_to.isoformat(),
        "finishedOperations": int(finished or 0),
        "redoOperations": int(redo or 0),
        "hoursWorked": _num(worked, 2),
        "targetHours": _num(target, 2),
        "laborEfficiencyPct": _num(labor_efficiency_pct(target, worked_on_target), 1),
    }


def _logged_hours(worker_id, start_utc, end_utc, now_utc) -> float:
    """Hours the worker's operations were running in [start, end), minus the daily break.

    An operation still running counts up to now.
    """
    from app.services.schedule_calendar import hours_excluding_break, merge_intervals

    op_ids = [
        oid
        for (oid,) in db.session.query(OperationTimeLog.operation_id)
        .join(JobOperation, JobOperation.id == OperationTimeLog.operation_id)
        .filter(
            JobOperation.crew_includes(worker_id),
            OperationTimeLog.event_at >= start_utc - timedelta(days=31),
            OperationTimeLog.event_at < end_utc,
        )
        .distinct()
    ]
    if not op_ids:
        return 0.0
    logs_by_op = defaultdict(list)
    for log in OperationTimeLog.query.filter(OperationTimeLog.operation_id.in_(op_ids)):
        logs_by_op[log.operation_id].append(log)
    intervals = []
    for logs in logs_by_op.values():
        spans = worked_intervals(logs)
        last = max(logs, key=lambda L: (ensure_utc(L.event_at), L.created_at or ensure_utc(L.event_at)))
        if last.event in (OperationTimeEvent.START, OperationTimeEvent.RESUME):
            spans.append((ensure_utc(last.event_at), now_utc))
        for s, e in spans:
            s, e = max(s, start_utc), min(e, end_utc, now_utc)
            if e > s:
                intervals.append((s, e))
    return sum(hours_excluding_break(s, e) for s, e in merge_intervals(intervals))


def _worker_week(worker_id, monday: date, through: date, now_utc) -> dict:
    figures = _worker_period_figures(worker_id, monday, through)
    figures["loggedHours"] = _num(
        _logged_hours(
            worker_id,
            shop_local_to_utc(monday, time(0, 0)),
            shop_local_to_utc(through + timedelta(days=1), time(0, 0)),
            now_utc,
        ),
        2,
    )
    return figures


def my_summary(worker_id):
    """A production worker's own figures for this week (from Monday), the same days
    of last week (for a fair comparison), and this month."""
    now = shop_now()
    today = now.date()
    now_utc = now.astimezone(timezone.utc)
    monday = today - timedelta(days=today.weekday())
    week_ago = timedelta(days=7)
    return {
        "thisWeek": _worker_week(worker_id, monday, today, now_utc),
        "lastWeek": _worker_week(worker_id, monday - week_ago, today - week_ago, now_utc),
        "thisMonth": _worker_period_figures(worker_id, today.replace(day=1), today),
    }


def overview(from_s=None, to_s=None):
    period_from, period_to, start_utc, end_utc = _parse_period(from_s, to_s)
    filters = _completed_in_period_filters(start_utc, end_utc)
    excluded = count_excluded_null_estimate(start_utc, end_utc)

    avg_var = (
        db.session.query(func.avg(JobOperation.variance_pct))
        .filter(
            *filters,
            JobOperation.estimated_hours.isnot(None),
            JobOperation.variance_pct.isnot(None),
        )
        .scalar()
    )
    with_variance = (
        db.session.query(func.count(JobOperation.id))
        .filter(
            *filters,
            JobOperation.estimated_hours.isnot(None),
            JobOperation.variance_pct.isnot(None),
        )
        .scalar()
        or 0
    )

    # Rework: follow-on rows (rework_of set). Count all follow-ons whose parent
    # completed in period; hours only from completed follow-ons in period.
    parent_ids_sq = (
        db.session.query(JobOperation.id)
        .filter(*filters)
        .scalar_subquery()
    )
    follow_on_count = (
        db.session.query(func.count(JobOperation.id))
        .filter(JobOperation.rework_of_operation_id.in_(parent_ids_sq))
        .scalar()
        or 0
    )
    rework_worked = float(
        db.session.query(func.coalesce(func.sum(JobOperation.actual_worked_hours), 0))
        .filter(*filters, JobOperation.rework_of_operation_id.isnot(None))
        .scalar()
        or 0
    )
    original_worked = float(
        db.session.query(func.coalesce(func.sum(JobOperation.actual_worked_hours), 0))
        .filter(*filters, JobOperation.rework_of_operation_id.is_(None))
        .scalar()
        or 0
    )
    total_worked = original_worked + rework_worked

    finished_ops, finished_redo_ops = (
        db.session.query(
            func.count(JobOperation.id),
            func.count(JobOperation.rework_of_operation_id),
        )
        .filter(*filters)
        .one()
    )

    open_dt = (
        MachineDowntime.query.filter(MachineDowntime.ended_at.is_(None)).count()
    )

    payload = _period_meta(period_from, period_to, excluded)
    payload.update(
        {
            "jobs": _finished_jobs_section(start_utc, end_utc),
            "efficiency": {
                "averageVariancePct": _num(avg_var),
                "completedOperationsWithVariance": int(with_variance),
            },
            "rework": {
                "count": int(follow_on_count),
                "workedHours": _num(rework_worked),
                "shareOfTotalWorkedHoursPct": _num(
                    (rework_worked / total_worked * 100) if total_worked else 0
                ),
                "finishedOperationCount": int(finished_ops or 0),
                "finishedRedoOperationCount": int(finished_redo_ops or 0),
                "redoRatePct": _num(
                    (finished_redo_ops / finished_ops * 100) if finished_ops else None
                ),
            },
            "downtime": {"openCount": int(open_dt)},
            "totals": {
                "originalWorkedHours": _num(original_worked),
                "reworkWorkedHours": _num(rework_worked),
                "totalWorkedHours": _num(total_worked),
            },
        }
    )
    return payload


def labor_efficiency_pct(target_hours, worked_hours):
    """Total target hours / total hours worked × 100; over 100 is faster than planned."""
    if target_hours is None or not worked_hours:
        return None
    return float(target_hours) / float(worked_hours) * 100.0


def _rework_hours_by(column, start_utc, end_utc):
    filters = _completed_in_period_filters(start_utc, end_utc) + [
        JobOperation.rework_of_operation_id.isnot(None),
        column.isnot(None),
    ]
    return dict(
        db.session.query(
            column,
            func.coalesce(func.sum(JobOperation.actual_worked_hours), 0),
        )
        .filter(*filters)
        .group_by(column)
        .all()
    )


def _rework_hours_by_crew_member(start_utc, end_utc):
    crew = JobOperation.crew_members_subquery()
    filters = _completed_in_period_filters(start_utc, end_utc) + [
        JobOperation.rework_of_operation_id.isnot(None),
    ]
    return dict(
        db.session.query(
            crew.c.worker_id,
            func.coalesce(func.sum(JobOperation.actual_worked_hours), 0),
        )
        .join(JobOperation, JobOperation.id == crew.c.operation_id)
        .filter(*filters)
        .group_by(crew.c.worker_id)
        .all()
    )


def efficiency_by_worker(from_s=None, to_s=None, min_ops=None):
    period_from, period_to, start_utc, end_utc = _parse_period(from_s, to_s)
    min_ops = DEFAULT_MIN_OPS if min_ops is None else int(min_ops)
    if min_ops < 1:
        raise AppError("minOps must be >= 1", "VALIDATION_ERROR", 400)
    excluded = count_excluded_null_estimate(start_utc, end_utc)
    crew = JobOperation.crew_members_subquery()

    rows = (
        db.session.query(
            User.id,
            User.full_name,
            func.count(JobOperation.id).label("op_count"),
            func.sum(JobOperation.estimated_hours).label("est"),
            func.sum(JobOperation.actual_worked_hours).label("act"),
            func.avg(JobOperation.variance_pct).label("avg_var"),
            func.sum(
                case(
                    (
                        (JobOperation.variance_pct >= -ON_ESTIMATE_BAND)
                        & (JobOperation.variance_pct <= ON_ESTIMATE_BAND),
                        1,
                    ),
                    else_=0,
                )
            ).label("on_est"),
        )
        .join(crew, crew.c.worker_id == User.id)
        .join(JobOperation, JobOperation.id == crew.c.operation_id)
        .filter(
            *_completed_in_period_filters(start_utc, end_utc),
            JobOperation.estimated_hours.isnot(None),
            JobOperation.variance_pct.isnot(None),
            JobOperation.rework_of_operation_id.is_(None),
        )
        .group_by(User.id, User.full_name)
        .having(func.count(JobOperation.id) >= min_ops)
        .order_by(func.avg(JobOperation.variance_pct).asc())
        .all()
    )
    rework = _rework_hours_by_crew_member(start_utc, end_utc)

    payload = _period_meta(period_from, period_to, excluded)
    payload["minimumOperationCount"] = min_ops
    payload["workers"] = [
        {
            "workerId": wid,
            "workerName": name,
            "operationCount": int(op_count),
            "totalEstimatedHours": _num(est),
            "totalActualWorkedHours": _num(act),
            "averageVariancePct": _num(avg_v),
            "laborEfficiencyPct": _num(labor_efficiency_pct(est, act)),
            "onEstimateRatePct": _num(
                (float(on_est) / float(op_count) * 100) if op_count else None
            ),
            "reworkWorkedHours": _num(float(rework.get(wid, 0) or 0)),
        }
        for wid, name, op_count, est, act, avg_v, on_est in rows
    ]
    payload["recommendationWeights"] = scoring_service.recommendation_weights()
    return payload


def efficiency_by_operation_type(from_s=None, to_s=None, min_ops=None):
    period_from, period_to, start_utc, end_utc = _parse_period(from_s, to_s)
    min_ops = DEFAULT_MIN_OPS if min_ops is None else int(min_ops)
    if min_ops < 1:
        raise AppError("minOps must be >= 1", "VALIDATION_ERROR", 400)
    excluded = count_excluded_null_estimate(start_utc, end_utc)

    rows = (
        db.session.query(
            OperationType.id,
            OperationType.code,
            OperationType.name,
            func.count(JobOperation.id).label("op_count"),
            func.sum(JobOperation.estimated_hours).label("est"),
            func.sum(JobOperation.actual_worked_hours).label("act"),
            func.avg(JobOperation.variance_pct).label("avg_var"),
            func.sum(
                case(
                    (
                        (JobOperation.variance_pct >= -ON_ESTIMATE_BAND)
                        & (JobOperation.variance_pct <= ON_ESTIMATE_BAND),
                        1,
                    ),
                    else_=0,
                )
            ).label("on_est"),
        )
        .join(JobOperation, JobOperation.operation_type_id == OperationType.id)
        .filter(
            *_completed_in_period_filters(start_utc, end_utc),
            JobOperation.estimated_hours.isnot(None),
            JobOperation.variance_pct.isnot(None),
            JobOperation.rework_of_operation_id.is_(None),
        )
        .group_by(OperationType.id, OperationType.code, OperationType.name)
        .having(func.count(JobOperation.id) >= min_ops)
        .order_by(func.avg(JobOperation.variance_pct).asc())
        .all()
    )
    rework = _rework_hours_by(JobOperation.operation_type_id, start_utc, end_utc)

    payload = _period_meta(period_from, period_to, excluded)
    payload["minimumOperationCount"] = min_ops
    payload["operationTypes"] = [
        {
            "operationTypeId": oid,
            "operationTypeCode": code,
            "operationTypeName": name,
            "operationCount": int(op_count),
            "totalEstimatedHours": _num(est),
            "totalActualWorkedHours": _num(act),
            "averageVariancePct": _num(avg_v),
            "laborEfficiencyPct": _num(labor_efficiency_pct(est, act)),
            "onEstimateRatePct": _num(
                (float(on_est) / float(op_count) * 100) if op_count else None
            ),
            "reworkWorkedHours": _num(float(rework.get(oid, 0) or 0)),
        }
        for oid, code, name, op_count, est, act, avg_v, on_est in rows
    ]
    return payload


def worked_intervals(logs) -> list[tuple[datetime, datetime]]:
    """Clock intervals from each START/RESUME to the next PAUSE/COMPLETE.

    Paused time (breaks, breakdowns, any pause reason) is never inside one.
    """
    ordered = sorted(
        logs or [],
        key=lambda L: (ensure_utc(L.event_at), L.created_at or ensure_utc(L.event_at)),
    )
    out = []
    open_start = None
    for log in ordered:
        at = ensure_utc(log.event_at)
        if log.event in (OperationTimeEvent.START, OperationTimeEvent.RESUME):
            if open_start is None:
                open_start = at
        elif log.event in (OperationTimeEvent.PAUSE, OperationTimeEvent.COMPLETE):
            if open_start is not None and at > open_start:
                out.append((open_start, at))
            open_start = None
    return out


def worked_busy_hours(logs, schedule_by_dow, exceptions_by_date, start_utc, end_utc) -> float:
    """Worked intervals clipped to [start, end) and to the worker's working hours."""
    total = 0.0
    for s, e in worked_intervals(logs):
        s, e = max(s, ensure_utc(start_utc)), min(e, ensure_utc(end_utc))
        if e <= s:
            continue
        segments = derive_working_segments(s, e, schedule_by_dow, exceptions_by_date)
        total += sum((se - ss).total_seconds() for ss, se in segments)
    return total / 3600.0


def _shop_available_hours(period_from: date, period_to: date) -> float:
    """Shop capacity from working hours + calendar exceptions (scheduler source)."""
    return shop_available_hours(period_from, period_to)


def type_utilization_pct(busy_hours_by_unit, available_hours_per_unit):
    """
    Type-level util = total busy / (available * unit_count)
    which equals the mean of per-unit utilization percentages.
    """
    if not busy_hours_by_unit or not available_hours_per_unit:
        return None
    n = len(busy_hours_by_unit)
    total_busy = sum(busy_hours_by_unit)
    denom = available_hours_per_unit * n
    if denom <= 0:
        return None
    return (total_busy / denom) * 100.0


def efficiency_by_machine(from_s=None, to_s=None, min_ops=None):
    period_from, period_to, start_utc, end_utc = _parse_period(from_s, to_s)
    min_ops = DEFAULT_MIN_OPS if min_ops is None else int(min_ops)
    if min_ops < 1:
        raise AppError("minOps must be >= 1", "VALIDATION_ERROR", 400)
    excluded = count_excluded_null_estimate(start_utc, end_utc)
    available = _shop_available_hours(period_from, period_to)
    exceptions = load_calendar_exceptions(period_from, period_to)

    active_types = (
        MachineType.query.order_by(MachineType.code).all()
    )
    active_units = (
        MachineUnit.query.filter_by(active=True)
        .order_by(MachineUnit.label)
        .all()
    )
    units_by_type = defaultdict(list)
    for u in active_units:
        units_by_type[u.machine_type_id].append(u)

    # Totals / variance sample per unit (original completed ops in period)
    def _new_stats():
        return {
            "op_count": 0,
            "est": 0.0,
            "act": 0.0,
            "var_sum": 0.0,
            "var_n": 0,
            "on_est": 0,
            "eff_est": 0.0,
            "eff_act": 0.0,
        }

    def _add_op(st, op):
        st["op_count"] += 1
        if op.estimated_hours is not None:
            st["est"] += float(op.estimated_hours)
        if op.actual_worked_hours is not None:
            st["act"] += float(op.actual_worked_hours)
        if op.estimated_hours is not None and op.actual_worked_hours is not None:
            st["eff_est"] += float(op.estimated_hours)
            st["eff_act"] += float(op.actual_worked_hours)
        if op.estimated_hours is not None and op.variance_pct is not None:
            st["var_n"] += 1
            st["var_sum"] += float(op.variance_pct)
            if -float(ON_ESTIMATE_BAND) <= float(op.variance_pct) <= float(
                ON_ESTIMATE_BAND
            ):
                st["on_est"] += 1

    unit_stats = defaultdict(_new_stats)
    type_stats = defaultdict(_new_stats)

    period_ops = JobOperation.query.filter(
        *_completed_in_period_filters(start_utc, end_utc),
        JobOperation.rework_of_operation_id.is_(None),
    ).all()

    for op in period_ops:
        if op.machine_unit_id:
            _add_op(unit_stats[op.machine_unit_id], op)
        if op.machine_type_id:
            _add_op(type_stats[op.machine_type_id], op)

    rework_type = _rework_hours_by(JobOperation.machine_type_id, start_utc, end_utc)
    rework_unit = _rework_hours_by(JobOperation.machine_unit_id, start_utc, end_utc)

    # Utilization: worked intervals in working hours / shop available hours
    util_ops = (
        JobOperation.query.options(joinedload(JobOperation.time_logs))
        .filter(
            *_completed_in_period_filters(start_utc, end_utc),
            JobOperation.actual_start.isnot(None),
            JobOperation.machine_unit_id.isnot(None),
            JobOperation.assigned_worker_id.isnot(None),
        )
        .all()
    )
    busy_by_unit = defaultdict(float)
    schedules = {}
    for op in util_ops:
        crew_key = tuple(op.crew_ids)
        if crew_key not in schedules:
            schedules[crew_key] = load_crew_schedule_map(crew_key)
        busy_by_unit[op.machine_unit_id] += worked_busy_hours(
            op.time_logs, schedules[crew_key], exceptions, start_utc, end_utc
        )

    def _variance_fields(st):
        """Null variance metrics when sample below minOps; totals always returned."""
        below = st["var_n"] < min_ops
        if below or st["var_n"] == 0:
            return None, None, True
        avg = st["var_sum"] / st["var_n"]
        on_rate = st["on_est"] / st["var_n"] * 100.0
        return avg, on_rate, False

    machine_units = []
    unit_util_by_type = defaultdict(list)
    for u in active_units:
        st = unit_stats[u.id]
        busy = busy_by_unit.get(u.id, 0.0)
        util = (busy / available * 100.0) if available else None
        avg_v, on_rate, below_flag = _variance_fields(st)
        # Also flag thin overall activity (no variance sample yet)
        if st["op_count"] < min_ops:
            below_flag = True
            avg_v, on_rate = None, None
        row = {
            "machineUnitId": u.id,
            "machineUnitLabel": u.label,
            "machineTypeId": u.machine_type_id,
            "machineTypeCode": u.machine_type.code if u.machine_type else None,
            "operationCount": int(st["op_count"]),
            "totalEstimatedHours": _num(st["est"]),
            "totalActualWorkedHours": _num(st["act"]),
            "averageVariancePct": _num(avg_v),
            "laborEfficiencyPct": (
                None if below_flag else _num(labor_efficiency_pct(st["eff_est"], st["eff_act"]))
            ),
            "onEstimateRatePct": _num(on_rate),
            "belowMinimumSample": below_flag,
            "reworkWorkedHours": _num(float(rework_unit.get(u.id, 0) or 0)),
            "busySegmentHours": _num(busy),
            "availableHours": _num(available),
            "utilizationPct": _num(util),
        }
        machine_units.append(row)
        unit_util_by_type[u.machine_type_id].append(util if util is not None else 0.0)

    machine_types = []
    for mt in active_types:
        units = units_by_type.get(mt.id, [])
        n_units = len(units)
        st = type_stats[mt.id]
        busy_list = [busy_by_unit.get(u.id, 0.0) for u in units]
        total_busy = sum(busy_list)
        type_available = available * n_units if n_units else None
        util = type_utilization_pct(busy_list, available) if n_units else None
        avg_v, on_rate, below_flag = _variance_fields(st)
        if st["op_count"] < min_ops:
            below_flag = True
            avg_v, on_rate = None, None
        machine_types.append(
            {
                "machineTypeId": mt.id,
                "machineTypeCode": mt.code,
                "machineTypeName": mt.name,
                "activeUnitCount": n_units,
                "operationCount": int(st["op_count"]),
                "totalEstimatedHours": _num(st["est"]),
                "totalActualWorkedHours": _num(st["act"]),
                "averageVariancePct": _num(avg_v),
                "laborEfficiencyPct": (
                    None
                    if below_flag
                    else _num(labor_efficiency_pct(st["eff_est"], st["eff_act"]))
                ),
                "onEstimateRatePct": _num(on_rate),
                "belowMinimumSample": below_flag,
                "reworkWorkedHours": _num(float(rework_type.get(mt.id, 0) or 0)),
                "busySegmentHours": _num(total_busy),
                "availableHours": _num(type_available),
                "utilizationPct": _num(util),
            }
        )

    # Stable order: types by code; units by type then label
    machine_types.sort(key=lambda r: r["machineTypeCode"] or "")
    machine_units.sort(
        key=lambda r: (r["machineTypeCode"] or "", r["machineUnitLabel"] or "")
    )

    payload = _period_meta(period_from, period_to, excluded)
    payload["minimumOperationCount"] = min_ops
    payload["availableHoursPerUnit"] = _num(available)
    payload["machineTypes"] = machine_types
    payload["machineUnits"] = machine_units
    return payload


def efficiency_trend(from_s=None, to_s=None):
    period_from, period_to, start_utc, end_utc = _parse_period(from_s, to_s)
    excluded = count_excluded_null_estimate(start_utc, end_utc)

    rows = (
        db.session.query(JobOperation.actual_end, JobOperation.variance_pct)
        .filter(
            *_completed_in_period_filters(start_utc, end_utc),
            JobOperation.estimated_hours.isnot(None),
            JobOperation.variance_pct.isnot(None),
        )
        .all()
    )
    buckets = defaultdict(lambda: {"ops": 0, "var_sum": 0.0, "jobs": 0})
    for actual_end, variance in rows:
        b = buckets[shop_week_start(actual_end)]
        b["ops"] += 1
        b["var_sum"] += float(variance)
    for _job, completed_at in _finished_jobs(start_utc, end_utc):
        buckets[shop_week_start(completed_at)]["jobs"] += 1

    weeks = [
        {
            "weekStart": ws.isoformat(),
            "operationCount": b["ops"],
            "averageVariancePct": _num(b["var_sum"] / b["ops"] if b["ops"] else None),
            "jobsFinished": b["jobs"],
        }
        for ws, b in sorted(buckets.items())
    ]

    payload = _period_meta(period_from, period_to, excluded)
    payload["weeks"] = weeks
    return payload


def delays(from_s=None, to_s=None):
    period_from, period_to, start_utc, end_utc = _parse_period(from_s, to_s)
    excluded = count_excluded_null_estimate(start_utc, end_utc)

    now_utc = shop_now().astimezone(timezone.utc)
    # Open downtime runs until now, never into the future.
    open_end = min(end_utc, max(now_utc, start_utc))

    # Pause time inside the period, finished operations or not.
    pauses = pause_intervals(start_utc, end_utc)
    pause_hours = defaultdict(float)
    pause_counts = defaultdict(int)
    for p in pauses:
        pause_hours[p["reason"]] += p["hours"]
        pause_counts[p["reason"]] += 1

    # Machine downtime in shop working hours, per unit (overlapping records once).
    dts = MachineDowntime.query.filter(
        MachineDowntime.started_at < end_utc,
        db.or_(
            MachineDowntime.ended_at.is_(None),
            MachineDowntime.ended_at > start_utc,
        ),
    ).all()
    dt_intervals = defaultdict(list)
    dt_counts = defaultdict(int)
    dt_open = defaultdict(int)
    for row in dts:
        clip_start = max(ensure_utc(row.started_at), start_utc)
        clip_end = min(ensure_utc(row.ended_at), end_utc) if row.ended_at else open_end
        if clip_end > clip_start:
            dt_intervals[row.machine_unit_id].append((clip_start, clip_end))
        dt_counts[row.machine_unit_id] += 1
        if row.ended_at is None:
            dt_open[row.machine_unit_id] += 1
    dt_hours = {
        uid: shop_working_hours(ivs) for uid, ivs in dt_intervals.items()
    }
    for uid in dt_counts:
        dt_hours.setdefault(uid, 0.0)
    total_downtime_hours = sum(dt_hours.values())

    # A "Machine down" pause on a unit that also has a downtime record covering
    # the same time is one breakdown: drop the overlap from the pause side.
    overlap_hours = 0.0
    down_pauses = defaultdict(list)
    for p in pauses:
        if p["reason"] == OperationPauseReason.MACHINE_DOWN.value and p["machineUnitId"]:
            down_pauses[p["machineUnitId"]].append((p["start"], p["end"]))
    overlap_by_unit = {}
    for uid, ivs in down_pauses.items():
        both = intersect_intervals(ivs, dt_intervals.get(uid, []))
        unit_overlap = sum((e - s).total_seconds() for s, e in both) / 3600.0
        overlap_by_unit[uid] = unit_overlap
        overlap_hours += unit_overlap
    if overlap_hours:
        key = OperationPauseReason.MACHINE_DOWN.value
        pause_hours[key] = max(pause_hours[key] - overlap_hours, 0.0)

    pause_breakdown = [
        {
            "reason": reason,
            "occurrenceCount": pause_counts[reason],
            "totalPausedHours": _num(pause_hours[reason]),
        }
        for reason in sorted(pause_hours.keys(), key=lambda r: -pause_hours[r])
    ]

    units = {}
    if dt_hours:
        units = {
            u.id: u
            for u in MachineUnit.query.filter(MachineUnit.id.in_(list(dt_hours.keys()))).all()
        }

    downtime_breakdown = []
    for uid, hrs in sorted(dt_hours.items(), key=lambda x: -x[1]):
        u = units.get(uid)
        downtime_breakdown.append(
            {
                "machineUnitId": uid,
                "machineUnitLabel": u.label if u else None,
                "machineTypeCode": (
                    u.machine_type.code if u and u.machine_type else None
                ),
                "occurrenceCount": dt_counts[uid],
                "totalDowntimeHours": _num(hrs),
                "openCount": dt_open[uid],
            }
        )

    # Rework split by the category recorded when the rework was raised.
    rework_by_category = (
        db.session.query(
            JobOperation.rework_reason_category,
            func.coalesce(func.sum(JobOperation.actual_worked_hours), 0),
            func.count(JobOperation.id),
        )
        .filter(
            *_completed_in_period_filters(start_utc, end_utc),
            JobOperation.rework_of_operation_id.isnot(None),
        )
        .group_by(JobOperation.rework_reason_category)
        .all()
    )

    # Pareto causes: pause reasons + machine downtime + rework.
    # Breaks and end-of-shift are normal non-working time, not delays.
    cause_rows = []
    excluded_pause_hours = {
        reason: pause_hours.get(reason, 0.0) for reason in NON_WORKING_PAUSE_REASONS
    }
    for reason, hrs in pause_hours.items():
        if reason in NON_WORKING_PAUSE_REASONS:
            continue
        cause_rows.append(
            {
                "cause": reason,
                "causeType": "PAUSE",
                "label": pause_reason_label(reason),
                "hours": float(hrs),
                "occurrenceCount": pause_counts[reason],
            }
        )
    if total_downtime_hours > 0 or sum(dt_counts.values()) > 0:
        cause_rows.append(
            {
                "cause": "MACHINE_DOWNTIME",
                "causeType": "DOWNTIME",
                "label": "Machine downtime",
                "hours": float(total_downtime_hours),
                "occurrenceCount": int(sum(dt_counts.values())),
            }
        )
    material = material_delays(start_utc, end_utc)
    for mcause, label in MATERIAL_CAUSE_LABEL.items():
        rows = [r for r in material if r["cause"] == mcause]
        if rows:
            cause_rows.append(
                {
                    "cause": PARETO_CAUSE[mcause],
                    "causeType": "MATERIAL",
                    "label": label,
                    "hours": float(sum(r["hours"] for r in rows)),
                    "occurrenceCount": len({r["jobOrderId"] for r in rows}),
                }
            )
    late_back = outsourced_delays(start_utc, end_utc)
    if late_back:
        cause_rows.append(
            {
                "cause": OUTSOURCED_CAUSE,
                "causeType": "OUTSOURCED",
                "label": OUTSOURCED_CAUSE_LABEL,
                "hours": float(sum(r["hours"] for r in late_back)),
                "occurrenceCount": len(late_back),
            }
        )
    for category, hrs, count in rework_by_category:
        if not (float(hrs or 0) > 0 or int(count or 0) > 0):
            continue
        key = category.value if category else "UNCATEGORISED"
        cause_rows.append(
            {
                "cause": f"REWORK:{key}",
                "causeType": "REWORK",
                "label": f"Rework — {_REWORK_CATEGORY_LABEL.get(key, key)}",
                "hours": float(hrs or 0),
                "occurrenceCount": int(count or 0),
            }
        )
    _attach_cause_details(
        cause_rows,
        pauses=pauses,
        overlap_by_unit=overlap_by_unit,
        dt_hours=dt_hours,
        dt_counts=dt_counts,
        dt_open=dt_open,
        material=material,
        late_back=late_back,
        rework_ops=_rework_ops(start_utc, end_utc),
    )
    # A row that rounds to 0.0 h (e.g. Machine down pauses fully covered by a
    # downtime record) explains no delay.
    cause_rows = [r for r in cause_rows if round(r["hours"], 1) > 0]
    pareto = pareto_rows(cause_rows)
    total_cause_hours = sum(r["hours"] for r in cause_rows)
    for row in pareto:
        for d in row.get("details") or []:
            d["hours"] = _num(d["hours"])
        row["hours"] = _num(row["hours"])
        row["shareOfTotalPct"] = _num(row["shareOfTotalPct"])
        row["cumulativePct"] = _num(row["cumulativePct"])

    payload = _period_meta(period_from, period_to, excluded)
    payload["pauseReasons"] = pause_breakdown
    payload["machineDowntime"] = downtime_breakdown
    payload["causes"] = pareto
    payload["paretoThresholdPct"] = PARETO_THRESHOLD_PCT
    payload["vitalFewCount"] = vital_few_count(pareto)
    payload["totalDelayHours"] = _num(total_cause_hours)
    payload["reworkByReason"] = sorted(
        (
            {
                "reason": (category.value if category else "UNCATEGORISED"),
                "label": _REWORK_CATEGORY_LABEL.get(
                    category.value if category else "UNCATEGORISED",
                    category.value if category else "UNCATEGORISED",
                ),
                "count": int(count or 0),
                "hours": _num(float(hrs or 0)),
            }
            for category, hrs, count in rework_by_category
            if int(count or 0) > 0
        ),
        key=lambda r: (-r["count"], -(r["hours"] or 0), r["label"]),
    )
    payload["materialDelays"] = [
        {**r, "hours": _num(r["hours"])} for r in material
    ]
    payload["outsourcedDelays"] = [
        {**r, "hours": _num(r["hours"])} for r in late_back
    ]
    payload["breakdownOverlapHours"] = _num(overlap_hours)
    payload["lateJobs"] = late_jobs(period_from, period_to)
    payload["excludedNonWorkingPauses"] = {
        "breakHours": _num(excluded_pause_hours[OperationPauseReason.BREAK.value]),
        "endOfShiftHours": _num(excluded_pause_hours[OperationPauseReason.END_OF_SHIFT.value]),
        "totalHours": _num(sum(excluded_pause_hours.values())),
    }
    return payload


PARETO_THRESHOLD_PCT = 80.0
CAUSE_DETAIL_LIMIT = 5


def _rework_ops(start_utc, end_utc):
    """Each redo operation finished in the period: where it ran and how long."""
    return (
        JobOperation.query.options(joinedload(JobOperation.job_order))
        .filter(
            *_completed_in_period_filters(start_utc, end_utc),
            JobOperation.rework_of_operation_id.isnot(None),
        )
        .all()
    )


def _detail_rows(groups, limit=CAUSE_DETAIL_LIMIT):
    """``groups``: label -> {"hours", "count", "jobs", "note"?}. Largest first,
    with the job numbers involved (up to three)."""
    rows = []
    for label, g in groups.items():
        jobs = sorted(j for j in g["jobs"] if j)
        rows.append(
            {
                "label": label,
                "hours": float(g["hours"]),
                "count": int(g["count"]),
                "jobs": jobs[:3],
                "moreJobs": max(len(jobs) - 3, 0),
                "note": g.get("note"),
            }
        )
    rows = [r for r in rows if round(r["hours"], 1) > 0 or r["count"]]
    rows.sort(key=lambda r: (-r["hours"], -r["count"], r["label"]))
    return rows[:limit], max(len(rows) - limit, 0)


def _attach_cause_details(cause_rows, *, pauses, overlap_by_unit, dt_hours, dt_counts, dt_open,
                          material, late_back, rework_ops):
    """Add ``details`` to each Pareto cause: which machine, supplier, outside shop
    or operation the lost hours came from, so the cause can be acted on.
    The chart itself is unchanged; the details are read under it."""
    unit_ids = {p["machineUnitId"] for p in pauses if p["machineUnitId"]} | set(dt_hours)
    unit_ids |= {op.machine_unit_id for op in rework_ops if op.machine_unit_id}
    units = (
        {u.id: u for u in MachineUnit.query.filter(MachineUnit.id.in_(list(unit_ids))).all()}
        if unit_ids
        else {}
    )
    job_ids = {p["jobOrderId"] for p in pauses if p["jobOrderId"]}
    jobs = (
        {j.id: j.job_number for j in JobOrder.query.filter(JobOrder.id.in_(list(job_ids))).all()}
        if job_ids
        else {}
    )

    def unit_label(uid):
        u = units.get(uid)
        return u.label if u else "No machine set"

    def group():
        return defaultdict(lambda: {"hours": 0.0, "count": 0, "jobs": set()})

    # Pause reasons: by machine unit (Machine down keeps the overlap rule).
    by_reason = defaultdict(group)
    for p in pauses:
        g = by_reason[p["reason"]][unit_label(p["machineUnitId"])]
        g["hours"] += p["hours"]
        g["count"] += 1
        g["jobs"].add(jobs.get(p["jobOrderId"]))
    down_key = OperationPauseReason.MACHINE_DOWN.value
    for uid, hrs in overlap_by_unit.items():
        g = by_reason[down_key].get(unit_label(uid))
        if g is not None:
            g["hours"] = max(g["hours"] - hrs, 0.0)

    # Machine downtime: by machine unit.
    downtime = {}
    for uid, hrs in dt_hours.items():
        open_n = dt_open.get(uid, 0)
        downtime[unit_label(uid)] = {
            "hours": hrs,
            "count": dt_counts.get(uid, 0),
            "jobs": set(),
            "note": "still down" if open_n else None,
        }

    # Material delays: by supplier (late deliveries) or by job (not ordered).
    by_material = defaultdict(group)
    for r in material:
        key = r["supplierNames"] if r["cause"] != "NOT_ORDERED" and r.get("supplierNames") else r["jobNumber"]
        g = by_material[r["cause"]][key or "Unknown"]
        g["hours"] += r["hours"]
        g["count"] += 1
        g["jobs"].add(r["jobNumber"])

    # Late back from an outside shop: by the shop it was sent to.
    outsourced = group()
    for r in late_back:
        g = outsourced[r.get("sentTo") or "Outside shop"]
        g["hours"] += r["hours"]
        g["count"] += 1
        g["jobs"].add(r["jobNumber"])

    # Redo: by operation and machine, per recorded category.
    by_rework = defaultdict(group)
    for op in rework_ops:
        key = op.rework_reason_category.value if op.rework_reason_category else "UNCATEGORISED"
        label = op.operation_name or "Operation"
        if op.machine_unit_id:
            label = f"{label} on {unit_label(op.machine_unit_id)}"
        g = by_rework[key][label]
        g["hours"] += float(op.actual_worked_hours or 0)
        g["count"] += 1
        g["jobs"].add(op.job_order.job_number if op.job_order else None)

    reverse_material = {v: k for k, v in PARETO_CAUSE.items()}
    for row in cause_rows:
        kind, cause = row["causeType"], row["cause"]
        if kind == "PAUSE":
            row["details"], row["moreDetails"] = _detail_rows(by_reason.get(cause, {}))
            row["detailBy"] = "machine"
        elif kind == "DOWNTIME":
            row["details"], row["moreDetails"] = _detail_rows(downtime)
            row["detailBy"] = "machine"
        elif kind == "MATERIAL":
            mcause = reverse_material.get(cause)
            row["details"], row["moreDetails"] = _detail_rows(by_material.get(mcause, {}))
            row["detailBy"] = "job" if mcause == "NOT_ORDERED" else "supplier"
        elif kind == "OUTSOURCED":
            row["details"], row["moreDetails"] = _detail_rows(outsourced)
            row["detailBy"] = "outside shop"
        elif kind == "REWORK":
            row["details"], row["moreDetails"] = _detail_rows(by_rework.get(cause.split(":", 1)[1], {}))
            row["detailBy"] = "operation"
        else:
            row["details"], row["moreDetails"] = [], 0
            row["detailBy"] = None



def pareto_rows(rows, value_key="hours"):
    """Rows sorted by ``value_key`` (largest first), each with its share of the
    total and the running cumulative share, both in %. Ties keep input order."""
    ordered = sorted(rows, key=lambda r: -float(r[value_key] or 0))
    total = sum(float(r[value_key] or 0) for r in ordered)
    cumulative = 0.0
    out = []
    for row in ordered:
        share = (float(row[value_key] or 0) / total * 100.0) if total else 0.0
        cumulative += share
        out.append({**row, "shareOfTotalPct": share, "cumulativePct": min(cumulative, 100.0)})
    return out


def vital_few_count(rows, threshold=PARETO_THRESHOLD_PCT):
    """How many leading causes it takes to reach ``threshold`` % of the total."""
    for i, row in enumerate(rows, start=1):
        if float(row["cumulativePct"] or 0) >= threshold - 1e-9:
            return i
    return len(rows)


NON_WORKING_PAUSE_REASONS = frozenset(
    {OperationPauseReason.BREAK.value, OperationPauseReason.END_OF_SHIFT.value}
)


_REWORK_CATEGORY_LABEL = {
    "DIMENSION_OUT_OF_TOLERANCE": "Dimension out of tolerance",
    "SURFACE_FINISH": "Surface finish",
    "WRONG_MATERIAL": "Wrong material",
    "MACHINE_FAULT": "Machine fault",
    "OPERATOR_ERROR": "Operator error",
    "OTHER": "Other",
    "UNCATEGORISED": "Uncategorised",
}


# --- Sales / demand forecasting (read-only) ---

FORECAST_HORIZON_WEEKS = 4
CAPACITY_LOAD_FLAG_PCT = 80.0


def _working_days_inclusive(d_from: date, d_to: date) -> int:
    if d_to < d_from:
        return 0
    n = 0
    d = d_from
    while d <= d_to:
        if d.weekday() < 6:
            n += 1
        d += timedelta(days=1)
    return n


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    import calendar

    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def month_partial_flags(period_from: date, period_to: date, year: int, month: int):
    """
    Flag a calendar month as partial when the analytics period does not cover
    the full month. Returns (partialPeriod, workingDaysCovered).
    """
    month_start, month_end = _month_bounds(year, month)
    cover_start = max(period_from, month_start)
    cover_end = min(period_to, month_end)
    if cover_end < cover_start:
        return True, 0
    partial = cover_start > month_start or cover_end < month_end
    return partial, _working_days_inclusive(cover_start, cover_end)


def _job_completion_shop_date(job: JobOrder) -> date | None:
    ends = [
        o.actual_end
        for o in (job.operations or [])
        if o.actual_end and o.status == OperationStatus.COMPLETED
    ]
    if not ends:
        return None
    return max(ends).astimezone(SHOP_TZ).date()


def _expected_completion_shop_date(job: JobOrder) -> date:
    ends = [o.scheduled_end for o in (job.operations or []) if o.scheduled_end]
    if ends:
        return max(ends).astimezone(SHOP_TZ).date()
    return job.due_date


SALES_STATUSES = (JobOrderStatus.COMPLETED, JobOrderStatus.FOR_DELIVERY, JobOrderStatus.DELIVERED)
PIPELINE_STATUSES = (JobOrderStatus.SCHEDULED, JobOrderStatus.IN_PROGRESS)


def _completed_jobs_in_period(period_from: date, period_to: date):
    jobs = (
        JobOrder.query.options(
            joinedload(JobOrder.operations),
            joinedload(JobOrder.client),
        )
        .filter(JobOrder.status.in_(SALES_STATUSES))
        .all()
    )
    out = []
    for job in jobs:
        done = _job_completion_shop_date(job)
        if done is None:
            continue
        if period_from <= done <= period_to:
            out.append((job, done))
    return out


def sales_summary(from_s=None, to_s=None):
    period_from, period_to, _start_utc, _end_utc = _parse_period(from_s, to_s)
    completed = _completed_jobs_in_period(period_from, period_to)

    by_month = defaultdict(lambda: {"amount": 0.0, "jobCount": 0})
    by_client = defaultdict(lambda: {"amount": 0.0, "jobCount": 0, "name": None})
    by_job_type = defaultdict(lambda: {"amount": 0.0, "jobCount": 0})

    for job, done in completed:
        amt = float(job.amount or 0)
        key = f"{done.year:04d}-{done.month:02d}"
        by_month[key]["amount"] += amt
        by_month[key]["jobCount"] += 1
        cid = job.client_id
        by_client[cid]["amount"] += amt
        by_client[cid]["jobCount"] += 1
        by_client[cid]["name"] = job.client.name if job.client else None
        jt = job.job_type.value if job.job_type else "UNKNOWN"
        by_job_type[jt]["amount"] += amt
        by_job_type[jt]["jobCount"] += 1

    months = []
    for key in sorted(by_month.keys()):
        year, month = int(key[:4]), int(key[5:7])
        partial, wd = month_partial_flags(period_from, period_to, year, month)
        row = by_month[key]
        months.append(
            {
                "month": key,
                "jobCount": row["jobCount"],
                "amount": _num(row["amount"], 2),
                "partialPeriod": partial,
                "workingDaysCovered": wd,
            }
        )

    clients = []
    for cid, row in by_client.items():
        n = row["jobCount"]
        clients.append(
            {
                "clientId": cid,
                "clientName": row["name"],
                "jobCount": n,
                "amount": _num(row["amount"], 2),
                "averageJobValue": _num(row["amount"] / n, 2) if n else None,
            }
        )
    clients.sort(key=lambda r: -(r["amount"] or 0))

    job_types = []
    for jt, row in sorted(by_job_type.items()):
        job_types.append(
            {
                "jobType": jt,
                "jobCount": row["jobCount"],
                "amount": _num(row["amount"], 2),
            }
        )

    total_amount = sum(float(j.amount or 0) for j, _ in completed)
    payload = {
        "period": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "workingDaysInPeriod": _working_days_inclusive(period_from, period_to),
        "completedJobCount": len(completed),
        "totalAmount": _num(total_amount, 2),
        "byMonth": months,
        "byClient": clients,
        "byJobType": job_types,
    }
    return payload


def sales_forecast(from_s=None, to_s=None):
    """Committed pipeline (fact) plus the monthly moving-average sales estimate.

    Both use all history, not the analytics period; from/to are accepted for
    API symmetry.
    """
    _ = from_s, to_s

    # Committed pipeline: released, not yet completed or delivered (fact)
    pipeline_jobs = (
        JobOrder.query.options(joinedload(JobOrder.operations))
        .filter(JobOrder.status.in_(PIPELINE_STATUSES))
        .all()
    )
    by_exp_month = defaultdict(lambda: {"amount": 0.0, "jobCount": 0})
    pipeline_total = 0.0
    for job in pipeline_jobs:
        amt = float(job.amount or 0)
        pipeline_total += amt
        exp = _expected_completion_shop_date(job)
        key = f"{exp.year:04d}-{exp.month:02d}"
        by_exp_month[key]["amount"] += amt
        by_exp_month[key]["jobCount"] += 1

    committed = {
        "label": "committedPipeline",
        "description": (
            "Released jobs not yet finished; pending jobs are "
            "left out (fact, not a forecast). "
            "Grouped by expected completion from scheduled_end when present, "
            "otherwise due_date."
        ),
        "totalAmount": _num(pipeline_total, 2),
        "jobCount": len(pipeline_jobs),
        "byExpectedCompletionMonth": [
            {
                "month": key,
                "jobCount": by_exp_month[key]["jobCount"],
                "amount": _num(by_exp_month[key]["amount"], 2),
            }
            for key in sorted(by_exp_month.keys())
        ],
    }

    return {
        "committedPipeline": committed,
        "salesForecast": forecast_service.sales_forecast(),
    }


def demand_forecast():
    return forecast_service.demand_forecast()


def consumable_replenishment():
    from app.services.replenishment_service import replenishment

    return replenishment()


def capacity_type_rows(capacity_rows):
    """Analytics rows from ``board_service.machine_capacity``: hours booked inside
    the window less hours already worked, over the shop hours of units that are
    not down (the same rule as the schedule board)."""
    rows = []
    for r in capacity_rows:
        avail = r["availableHours"] or 0.0
        pct = r["loadPct"] if avail > 0 else None
        rows.append(
            {
                "machineTypeId": r["machineTypeId"],
                "machineTypeCode": r["machineTypeCode"],
                "machineTypeName": r["machineTypeName"],
                "activeUnitCount": r["units"],
                "unitsDown": r["unitsDown"],
                "availableHours": _num(avail, 2),
                "bookedHours": r["bookedHours"],
                "workedHours": r["workedHours"],
                "scheduledLoadHours": _num(r["loadHours"], 2),
                "projectedLoadPct": _num(pct, 2) if pct is not None else None,
                "above80Pct": bool(pct is not None and pct >= CAPACITY_LOAD_FLAG_PCT),
            }
        )
    return rows


def demand_capacity(from_s=None, to_s=None):
    """
    Next-4-weeks load vs available hours × active unit count.
    Optional from/to are ignored for the horizon (fixed forward window) but
    accepted for API symmetry; sample metadata still uses shop 'today'.
    """
    _ = from_s, to_s  # horizon is always forward-looking
    today = shop_now().date()
    horizon_from = today
    horizon_to = today + timedelta(days=FORECAST_HORIZON_WEEKS * 7 - 1)
    horizon_wd = _working_days_inclusive(horizon_from, horizon_to)
    available_per_unit = float(shop_available_hours(horizon_from, horizon_to))
    start_utc = shop_local_to_utc(horizon_from, time(0, 0))
    end_utc = shop_local_to_utc(horizon_to + timedelta(days=1), time(0, 0))

    from app.services.board_service import capacity_for_period

    scheduled_ops = (
        JobOperation.query.join(JobOrder)
        .filter(
            JobOrder.status != JobOrderStatus.COMPLETED,
            JobOperation.status != OperationStatus.COMPLETED,
            JobOperation.scheduled_start.isnot(None),
            JobOperation.scheduled_end.isnot(None),
            JobOperation.scheduled_start < end_utc,
            JobOperation.scheduled_end > start_utc,
        )
        .count()
    )
    thin = scheduled_ops == 0
    machine_types = capacity_type_rows(capacity_for_period(start_utc, end_utc))

    payload = {
        "horizon": {
            "from": horizon_from.isoformat(),
            "to": horizon_to.isoformat(),
        },
        "horizonWorkingDays": horizon_wd,
        "availableHoursPerUnit": _num(available_per_unit, 2),
        "scheduledOperationsInHorizon": scheduled_ops,
        "thinSample": thin,
        "machineTypes": machine_types,
    }
    if thin:
        payload["thinSampleNote"] = (
            "No scheduled operations in the next 4 weeks; "
            "expected workload is zero until open operations have scheduled times."
        )
    return payload


# --- Pure helpers for unit tests ---

def average_variance_pct(operations):
    """Exclude null estimated_hours from the variance average."""
    vals = [
        float(o.variance_pct)
        for o in operations
        if o.estimated_hours is not None
        and o.variance_pct is not None
    ]
    if not vals:
        return None
    return sum(vals) / len(vals)


def split_worked_hours(operations):
    """Separate original vs rework worked hours."""
    original = sum(
        float(o.actual_worked_hours or 0)
        for o in operations
        if o.rework_of_operation_id is None
    )
    rework = sum(
        float(o.actual_worked_hours or 0)
        for o in operations
        if o.rework_of_operation_id is not None
    )
    return original, rework


def utilization_from_segments(segment_hours, available_hours):
    if not available_hours:
        return None
    return segment_hours / available_hours * 100.0


def filter_by_min_ops(groups, min_ops):
    """groups: iterable of dicts with operationCount."""
    return [g for g in groups if g.get("operationCount", 0) >= min_ops]


def purchasing_summary(from_s=None, to_s=None):
    """
    Raw-material purchase analytics for the period (by date_ordered).
    - materials by purchase count and total spend
    - spend by supplier
    - supplier lead time: avg actual days vs stated typical lead time, one
      sample per supplier order (issued -> fully received); lines recorded
      without a PO are one sample each (ordered -> received)
    Draft lines (no order date yet) and cancelled lines are left out.
    """
    from app.models.material_purchase import MaterialPurchase
    from app.models.supplier import Supplier
    from app.models.supplier_order import SupplierOrder, SupplierOrderStatus

    period_from, period_to, _start_utc, _end_utc = _parse_period(from_s, to_s)

    purchases = (
        MaterialPurchase.query.filter(
            MaterialPurchase.date_ordered >= period_from,
            MaterialPurchase.date_ordered <= period_to,
            MaterialPurchase.cancelled_at.is_(None),
        )
        .all()
    )

    by_material = defaultdict(
        lambda: {"purchaseCount": 0, "totalQuantity": 0.0, "totalSpend": 0.0, "unit": None}
    )
    by_supplier_spend = defaultdict(
        lambda: {
            "supplierId": None,
            "supplierName": None,
            "purchaseCount": 0,
            "totalSpend": 0.0,
        }
    )
    lead_samples = defaultdict(list)  # supplier_id -> list of actual days

    for p in purchases:
        key = (p.material_name or "").strip().lower()
        row = by_material[key]
        row["materialName"] = p.material_name
        row["purchaseCount"] += 1
        row["totalQuantity"] += float(p.quantity or 0)
        spend = float(p.quantity or 0) * float(p.unit_cost or 0)
        row["totalSpend"] += spend
        if p.unit and not row["unit"]:
            row["unit"] = p.unit

        sid = p.supplier_id
        srow = by_supplier_spend[sid]
        srow["supplierId"] = sid
        srow["supplierName"] = p.supplier.name if p.supplier else None
        srow["purchaseCount"] += 1
        srow["totalSpend"] += spend

        if p.supplier_order_id is None and p.date_ordered and p.date_received:
            lead_samples[sid].append((p.date_received - p.date_ordered).days)

    received_orders = SupplierOrder.query.filter(
        SupplierOrder.status == SupplierOrderStatus.RECEIVED,
        SupplierOrder.date_issued >= period_from,
        SupplierOrder.date_issued <= period_to,
        SupplierOrder.received_date.isnot(None),
    ).all()
    for o in received_orders:
        lead_samples[o.supplier_id].append((o.received_date - o.date_issued).days)

    materials_by_count = sorted(
        (
            {
                "materialName": v["materialName"],
                "purchaseCount": v["purchaseCount"],
                "totalQuantity": _num(v["totalQuantity"], 2),
                "totalSpend": _num(v["totalSpend"], 2),
                "unit": v["unit"],
            }
            for v in by_material.values()
        ),
        key=lambda r: (-r["purchaseCount"], -float(r["totalSpend"] or 0)),
    )
    materials_by_spend = sorted(
        materials_by_count,
        key=lambda r: (-float(r["totalSpend"] or 0), -r["purchaseCount"]),
    )

    spend_by_supplier = sorted(
        (
            {
                "supplierId": v["supplierId"],
                "supplierName": v["supplierName"],
                "purchaseCount": v["purchaseCount"],
                "totalSpend": _num(v["totalSpend"], 2),
            }
            for v in by_supplier_spend.values()
        ),
        key=lambda r: -float(r["totalSpend"] or 0),
    )

    # Lead time: include suppliers that have samples in period; also stated lead time
    supplier_ids = set(lead_samples.keys()) | {
        v["supplierId"] for v in by_supplier_spend.values()
    }
    suppliers = {
        s.id: s
        for s in Supplier.query.filter(Supplier.id.in_(list(supplier_ids) or ["__none__"])).all()
    } if supplier_ids else {}

    lead_time = []
    for sid in supplier_ids:
        s = suppliers.get(sid)
        samples = lead_samples.get(sid) or []
        avg_actual = (sum(samples) / len(samples)) if samples else None
        stated = s.typical_lead_time_days if s else None
        variance = None
        if avg_actual is not None and stated is not None:
            variance = avg_actual - stated
        lead_time.append(
            {
                "supplierId": sid,
                "supplierName": s.name if s else None,
                "statedLeadTimeDays": stated,
                "sampleCount": len(samples),
                "averageActualDays": _num(avg_actual, 2) if avg_actual is not None else None,
                "varianceDays": _num(variance, 2) if variance is not None else None,
            }
        )
    lead_time.sort(
        key=lambda r: (
            -(r["sampleCount"] or 0),
            r["supplierName"] or "",
        )
    )

    total_spend = sum(float(p.quantity or 0) * float(p.unit_cost or 0) for p in purchases)

    return {
        "period": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "purchaseCount": len(purchases),
        "totalSpend": _num(total_spend, 2),
        "materialsByCount": materials_by_count,
        "materialsBySpend": materials_by_spend,
        "spendBySupplier": spend_by_supplier,
        "supplierLeadTime": lead_time,
    }


# --- Resource usage by week (objective 4.1) ---


def _period_weeks(period_from: date, period_to: date):
    """[(monday, first day, last day)] for every Monday-start week in the period,
    clipped to the period."""
    out = []
    monday = period_from - timedelta(days=period_from.weekday())
    while monday <= period_to:
        out.append((monday, max(monday, period_from), min(monday + timedelta(days=6), period_to)))
        monday += timedelta(days=7)
    return out


def _running_spans(logs, now_utc):
    """Worked intervals, plus the span still running when the last event is a
    START or RESUME (up to now)."""
    spans = worked_intervals(logs)
    if logs:
        last = max(logs, key=lambda L: (ensure_utc(L.event_at), L.created_at or ensure_utc(L.event_at)))
        if last.event in (OperationTimeEvent.START, OperationTimeEvent.RESUME):
            spans.append((ensure_utc(last.event_at), now_utc))
    return spans


def utilization_by_week(from_s=None, to_s=None):
    """Machine unit x week: hours an operation was running on the unit inside the
    crew's working hours, over the shop's available hours that week."""
    period_from, period_to, start_utc, end_utc = _parse_period(from_s, to_s)
    now_utc = shop_now().astimezone(timezone.utc)
    exceptions = load_calendar_exceptions(period_from, period_to)
    weeks = []
    for monday, first, last in _period_weeks(period_from, period_to):
        weeks.append(
            {
                "weekStart": monday.isoformat(),
                "from": first.isoformat(),
                "to": last.isoformat(),
                "availableHours": float(shop_available_hours(first, last)),
                "_start": shop_local_to_utc(first, time(0, 0)),
                "_end": shop_local_to_utc(last + timedelta(days=1), time(0, 0)),
            }
        )

    op_ids = [
        oid
        for (oid,) in db.session.query(OperationTimeLog.operation_id)
        .filter(
            OperationTimeLog.event_at >= start_utc - timedelta(days=31),
            OperationTimeLog.event_at < end_utc,
        )
        .distinct()
    ]
    ops = (
        JobOperation.query.options(joinedload(JobOperation.time_logs))
        .filter(
            JobOperation.id.in_(op_ids or ["__none__"]),
            JobOperation.machine_unit_id.isnot(None),
            not_outsourced_filter(),
        )
        .all()
    )
    busy = defaultdict(lambda: defaultdict(float))
    schedules = {}
    for op in ops:
        spans = _running_spans(op.time_logs, now_utc)
        if not spans:
            continue
        crew_key = tuple(op.crew_ids)
        if crew_key not in schedules:
            schedules[crew_key] = load_crew_schedule_map(crew_key)
        for w in weeks:
            for s, e in spans:
                s, e = max(s, w["_start"]), min(e, w["_end"], now_utc)
                if e <= s:
                    continue
                segments = derive_working_segments(s, e, schedules[crew_key], exceptions)
                busy[op.machine_unit_id][w["weekStart"]] += (
                    sum((se - ss).total_seconds() for ss, se in segments) / 3600.0
                )

    units = (
        MachineUnit.query.filter_by(active=True)
        .join(MachineType, MachineType.id == MachineUnit.machine_type_id)
        .order_by(MachineType.code, MachineUnit.label)
        .all()
    )
    machines = []
    for u in units:
        cells = []
        for w in weeks:
            hours = busy[u.id].get(w["weekStart"], 0.0)
            avail = w["availableHours"]
            cells.append(
                {
                    "weekStart": w["weekStart"],
                    "busyHours": _num(hours, 1),
                    "utilizationPct": _num(min(hours / avail * 100.0, 100.0), 1) if avail else None,
                }
            )
        total_busy = sum(busy[u.id].values())
        total_avail = sum(w["availableHours"] for w in weeks)
        machines.append(
            {
                "machineUnitId": u.id,
                "machineUnitLabel": u.label,
                "machineTypeCode": u.machine_type.code if u.machine_type else None,
                "machineTypeName": u.machine_type.name if u.machine_type else None,
                "busyHours": _num(total_busy, 1),
                "utilizationPct": _num(total_busy / total_avail * 100.0, 1) if total_avail else None,
                "cells": cells,
            }
        )
    return {
        "period": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "weeks": [{k: v for k, v in w.items() if not k.startswith("_")} for w in weeks],
        "machines": machines,
    }


# --- Job-completion estimates for open jobs (objective 4.3) ---


def completion_estimates():
    """Released jobs: predicted finish from past performance vs the date required,
    and the ratios the prediction uses."""
    from app.services import completion_estimate_service as est
    from app.services.job_order_service import job_list_load_options

    jobs = (
        JobOrder.query.options(*job_list_load_options())
        .filter(JobOrder.status.in_(est.RELEASED))
        .all()
    )
    ratios = est.type_ratios()
    details = est.detail_ratios()
    now = datetime.now(timezone.utc)
    rows = []
    for job in jobs:
        state = est.risk_state(job, now)
        predicted = state["predicted"]
        scheduled = state["scheduled"]
        later = max((d for d in (predicted, scheduled) if d), default=None)
        finish_day = later.astimezone(SHOP_TZ).date() if later else None
        bases = sorted(
            {o.get("basis") for o in (state["estimate"] or {}).get("operations", []) if o.get("basis")}
        )
        rows.append(
            {
                "jobOrderId": job.id,
                "jobNumber": job.job_number,
                "title": job.title,
                "clientName": job.client.name if job.client else None,
                "status": job.status.value,
                "dueDate": job.due_date.isoformat() if job.due_date else None,
                "scheduledFinish": scheduled.isoformat() if scheduled else None,
                "predictedFinish": predicted.isoformat() if predicted else None,
                "basis": state["basis"],
                "flag": state["flag"],
                "slackDays": (job.due_date - finish_day).days if finish_day and job.due_date else None,
                "ratioBases": bases,
            }
        )
    flag_order = {"RED": 0, "AMBER": 1, "GREEN": 2, None: 3}
    rows.sort(key=lambda r: (flag_order.get(r["flag"], 3), r["slackDays"] if r["slackDays"] is not None else 9999))
    type_rows = sorted(
        (
            {
                "operationTypeId": tid,
                "operationTypeName": info["operationTypeName"],
                "samples": info["samples"],
                "ratio": info["ratio"],
                "enoughHistory": info["enoughHistory"],
                "workerRatios": sum(1 for k in details if k[0] == "WORKER" and k[1] == tid),
                "machineRatios": sum(1 for k in details if k[0] == "MACHINE" and k[1] == tid),
            }
            for tid, info in ratios.items()
        ),
        key=lambda r: (-r["samples"], r["operationTypeName"] or ""),
    )
    return {
        "minSamples": est.MIN_SAMPLES,
        "jobs": rows,
        "operationTypes": type_rows,
    }
