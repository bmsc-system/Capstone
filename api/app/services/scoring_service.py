"""Weighted scoring components for worker recommendation.

Fixed shop weights (not editable), one set per kind of suggestion:
  machine lead:   assigned operator 0.40 · skill 0.30 · past performance 0.20 · workload 0.10
  no machine:     past performance 0.60 · workload 0.40
  helper:         past performance 0.50 · workload 0.50

Availability is a hard filter in worker_suggestion_service, not a weight.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

from app.models.operation import JobOperation, OperationStatus
from app.models.worker_skill import WorkCalendarException, WorkerSchedule
from app.services.schedule_calendar import build_worker_working_windows, effective_hours_for_date
from app.services.worker_availability import _parse_dt, _windows_overlap, list_worker_operations

logger = logging.getLogger(__name__)

# Single source of truth for suggestion ranking weights ("efficiency" is past
# performance: target hours against hours worked).
MACHINE_LEAD_WEIGHTS = {
    "operator": 0.40,
    "skill": 0.30,
    "efficiency": 0.20,
    "workload": 0.10,
}
NO_MACHINE_WEIGHTS = {
    "efficiency": 0.60,
    "workload": 0.40,
}
HELPER_WEIGHTS = {
    "efficiency": 0.50,
    "workload": 0.50,
}

COLD_START_MIN_SAMPLES = 2
EFFICIENCY_RATIO_CAP = 1.5


def recommendation_weights():
    """All three weight sets, for the "How workers are recommended" panel."""
    return {
        "machineLead": dict(MACHINE_LEAD_WEIGHTS),
        "noMachine": dict(NO_MACHINE_WEIGHTS),
        "helper": dict(HELPER_WEIGHTS),
        "minimumSamples": COLD_START_MIN_SAMPLES,
    }


def validate_weights_sum(weights, tolerance=1e-6):
    total = sum(float(v) for v in weights.values())
    return abs(total - 1.0) <= tolerance, total


def score_skill(proficiency=None, is_primary=False):
    """
    Returns (score 0..1, reason fragment, used_default).
    No skill row → 0.0 (not a default — measured absence).
    """
    if proficiency is None:
        return 0.0, "no skill for this machine", False
    raw = float(proficiency) / 5.0
    if is_primary:
        raw = min(1.0, raw + 0.1)
    else:
        raw = min(1.0, max(0.0, raw))
    primary_note = ", primary skill" if is_primary else ""
    return raw, f"proficiency {int(proficiency)}{primary_note}", False


def _window_vs_hours(window_start, window_end, day_start_t, day_end_t, on_date):
    """
    Classify how much of the window on `on_date` falls inside working hours.
    Returns 'inside' | 'partial' | 'outside' | 'none' (no overlap with that day).
    """
    day_start = datetime.combine(on_date, time.min, tzinfo=window_start.tzinfo)
    day_end = day_start + timedelta(days=1)
    seg_start = max(window_start, day_start)
    seg_end = min(window_end, day_end)
    if seg_start >= seg_end:
        return "none"

    if day_start_t is None or day_end_t is None:
        return "outside"

    work_start = datetime.combine(on_date, day_start_t, tzinfo=window_start.tzinfo)
    work_end = datetime.combine(on_date, day_end_t, tzinfo=window_start.tzinfo)
    if seg_start >= work_start and seg_end <= work_end:
        return "inside"
    if seg_end <= work_start or seg_start >= work_end:
        return "outside"
    return "partial"


def score_availability(
    worker_id,
    scheduled_start=None,
    scheduled_end=None,
    exclude_operation_id=None,
    *,
    schedules=None,
    exceptions=None,
    operations=None,
):
    """
    Calendar/overlap probe used by tests and diagnostics.
    Not a scoring weight — suggestions filter busy workers separately.
    Returns (score, reason fragment, used_default).
    """
    from app.services.schedule_calendar import derive_working_segments

    start = _parse_dt(scheduled_start)
    end = _parse_dt(scheduled_end)

    if not start or not end or end <= start:
        return (
            0.5,
            "no proposed window yet (neutral default)",
            True,
        )

    if schedules is None:
        schedules = WorkerSchedule.query.filter_by(worker_id=worker_id).all()
    schedule_by_dow = {s.day_of_week: s for s in schedules}

    from app.services.schedule_calendar import utc_to_shop

    shop_start = utc_to_shop(start)
    shop_end = utc_to_shop(end)
    d0 = shop_start.date()
    d1 = shop_end.date()
    if exceptions is None:
        exceptions = WorkCalendarException.query.filter(
            WorkCalendarException.date >= d0,
            WorkCalendarException.date <= d1,
        ).all()
    exceptions_by_date = {e.date: e for e in exceptions}

    proposed_segments = derive_working_segments(
        start, end, schedule_by_dow, exceptions_by_date
    )
    if not proposed_segments:
        return 0.0, "outside working hours", False

    if operations is None:
        operations = list_worker_operations(
            worker_id, exclude_operation_id=exclude_operation_id
        )
    for op in operations:
        if not op.scheduled_start or not op.scheduled_end:
            continue
        op_segments = derive_working_segments(
            op.scheduled_start,
            op.scheduled_end,
            schedule_by_dow,
            exceptions_by_date,
        )
        for a_start, a_end in proposed_segments:
            for b_start, b_end in op_segments:
                if _windows_overlap(a_start, a_end, b_start, b_end):
                    label = op.operation_name or "another operation"
                    return 0.0, f"conflicts with '{label}'", False

    day_statuses = []
    for seg_start, seg_end in proposed_segments:
        seg_shop_start = utc_to_shop(seg_start)
        seg_shop_end = utc_to_shop(seg_end)
        cur = seg_shop_start.date()
        last = seg_shop_end.date()
        while cur <= last:
            day_start_t, day_end_t, is_working = effective_hours_for_date(
                cur, schedule_by_dow, exceptions_by_date
            )
            if not is_working:
                day_start_t, day_end_t = None, None
            status = _window_vs_hours(
                seg_shop_start, seg_shop_end, day_start_t, day_end_t, cur
            )
            if status != "none":
                day_statuses.append(status)
            cur += timedelta(days=1)

    if not day_statuses:
        return 0.0, "outside working hours", False
    if all(s == "inside" for s in day_statuses):
        return 1.0, "free that window", False
    if all(s == "outside" for s in day_statuses):
        return 0.0, "outside working hours", False
    return 0.5, "window partly outside working hours", False


def current_week_bounds(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_monday = monday + timedelta(days=7)
    return monday, next_monday


def worker_week_load_hours(worker_id, now=None, exclude_operation_id=None, operations=None):
    """Sum estimated_hours for SCHEDULED/IN_PROGRESS ops in the current week."""
    week_start, week_end = current_week_bounds(now)
    if operations is None:
        operations = JobOperation.query.filter(
            JobOperation.crew_includes(worker_id),
            JobOperation.status.in_(
                (OperationStatus.SCHEDULED, OperationStatus.IN_PROGRESS)
            ),
        ).all()

    total = 0.0
    for op in operations:
        if exclude_operation_id and op.id == exclude_operation_id:
            continue
        if op.status not in (OperationStatus.SCHEDULED, OperationStatus.IN_PROGRESS):
            continue
        if op.scheduled_start is not None:
            ss = op.scheduled_start
            if ss.tzinfo is None:
                ss = ss.replace(tzinfo=timezone.utc)
            if ss < week_start or ss >= week_end:
                se = op.scheduled_end
                if se is None:
                    continue
                if se.tzinfo is None:
                    se = se.replace(tzinfo=timezone.utc)
                if se <= week_start or ss >= week_end:
                    continue
        hours = float(op.estimated_hours or 0)
        total += hours
    return total


def worker_week_hours(schedule_by_dow, exceptions_by_date, now=None) -> float:
    """The worker's own working hours this week, with holidays and overtime days."""
    week_start, week_end = current_week_bounds(now)
    windows = build_worker_working_windows(schedule_by_dow, exceptions_by_date, week_start, week_end)
    return sum((end - start).total_seconds() for start, end in windows) / 3600


def score_workload(worker_hours, week_hours):
    """
    Share of the worker's working hours this week that is still free:
    nothing booked → 1.0, a full week → 0.0.

    Booked hours are compared with the worker's own week, not with the busiest
    peer, so a worker with an empty week gains only what his free time is
    worth instead of the full weight over people with a few hours booked.
    Returns (score, reason fragment, used_default).
    """
    booked = max(0.0, float(worker_hours or 0))
    week = float(week_hours or 0)
    if week <= 0:
        return (1.0 if booked == 0 else 0.0), f"no working hours this week ({booked:.1f}h booked)", True

    score = max(0.0, min(1.0, 1 - booked / week))
    if score >= 0.75:
        label = "light load this week"
    elif score <= 0.25:
        label = "heavy load this week"
    else:
        label = "moderate load this week"
    return score, f"{label} ({booked:.1f}h of {week:.0f}h)", False


def score_efficiency(completed_pairs):
    """
    completed_pairs: iterable of (estimated_hours, actual_hours).
    Cold start (<2) → 0.5 default.
    Returns (score, reason fragment, used_default).
    """
    pairs = [
        (float(e), float(a))
        for e, a in completed_pairs
        if e is not None and a is not None and float(a) > 0
    ]
    if len(pairs) < COLD_START_MIN_SAMPLES:
        return (
            0.5,
            "too few completed ops yet (neutral default)",
            True,
        )

    ratios = []
    for est, act in pairs:
        ratio = est / act
        ratio = max(0.0, min(EFFICIENCY_RATIO_CAP, ratio))
        ratios.append(ratio / EFFICIENCY_RATIO_CAP)

    avg = sum(ratios) / len(ratios)
    return avg, f"past performance from {len(pairs)} completed ops", False


def combine_score(weights, components):
    total = sum(float(w) * float(components[key]) for key, w in weights.items())
    return round(max(0.0, min(1.0, total)), 4)


def _pairs_from_ops(ops):
    """Worked hours exclude pauses, so breaks and overnight gaps don't count against the worker."""
    pairs = []
    for op in ops:
        if op.actual_worked_hours is None or op.estimated_hours is None:
            continue
        actual_hours = float(op.actual_worked_hours)
        if actual_hours <= 0:
            continue
        pairs.append((float(op.estimated_hours), actual_hours))
    return pairs


def fetch_efficiency_pairs(worker_id, operation_type_id):
    """
    Prefer completed ops of the same operation type; if fewer than the cold-start
    minimum, fall back to this worker's completed ops across all types.
    """
    type_pairs = []
    if operation_type_id:
        type_ops = JobOperation.query.filter(
            JobOperation.crew_includes(worker_id),
            JobOperation.operation_type_id == operation_type_id,
            JobOperation.status == OperationStatus.COMPLETED,
            JobOperation.actual_worked_hours.isnot(None),
            JobOperation.estimated_hours.isnot(None),
        ).all()
        type_pairs = _pairs_from_ops(type_ops)
        if len(type_pairs) >= COLD_START_MIN_SAMPLES:
            return type_pairs

    from app.services.analytics_service import not_outsourced_filter

    all_ops = JobOperation.query.filter(
        JobOperation.crew_includes(worker_id),
        JobOperation.status == OperationStatus.COMPLETED,
        JobOperation.actual_worked_hours.isnot(None),
        JobOperation.estimated_hours.isnot(None),
        not_outsourced_filter(),
    ).all()
    all_pairs = _pairs_from_ops(all_ops)
    if len(all_pairs) >= COLD_START_MIN_SAMPLES:
        return all_pairs
    # Prefer typed pairs when present but still short; else whatever we have.
    return type_pairs if type_pairs else all_pairs


def log_weights_used(weights, context="suggest"):
    logger.info(
        "scoring weights used (%s): %s",
        context,
        " ".join(f"{k}={float(v):.2f}" for k, v in weights.items()),
    )
