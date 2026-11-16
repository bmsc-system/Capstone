"""Earliest-fit scheduling — propose windows without persisting."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from app.constants.scheduling import DEFAULT_ESTIMATED_HOURS, SCHEDULE_HORIZON_DAYS
from app.models.machine import MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.services.schedule_calendar import (
    build_worker_working_windows,
    derive_working_segments,
    effective_hours_for_date,
    ensure_utc,
    has_working_hours,
    horizon_end_utc,
    intersect_intervals,
    load_calendar_exceptions,
    load_crew_schedule_map,
    merge_intervals,
    place_duration,
    place_unbroken,
    serialize_segments,
    shop_local_to_utc,
    shop_now,
    utc_to_shop,
)

MISSING_WORKER_MESSAGE = "assign a worker to schedule this operation"

# Only released jobs reserve time; a pending job's saved times block nobody.
BOOKING_STATUSES = (
    OperationStatus.SCHEDULED,
    OperationStatus.IN_PROGRESS,
    OperationStatus.COMPLETED,
    OperationStatus.REWORK,
)

FROZEN_STATUSES = (OperationStatus.COMPLETED, OperationStatus.IN_PROGRESS)

MIN_REMAINING_HOURS = 1.0

# A faster unit is suggested only when the job would finish at least this much earlier.
FASTER_UNIT_MIN_GAIN = timedelta(hours=4)
FASTER_UNIT_MAX = 5
# Late operations checked for a faster worker + unit, and full re-plans tried for each.
FASTER_PAIR_OPS = 2
FASTER_PAIR_TRIES = 4

# Paused this many working hours of its crew (one working day) without a resume: stalled.
STALL_AFTER_WORKING_HOURS = 8.0


def compute_schedule_flag(projected_completion_utc, due_date) -> str | None:
    """
    Compare projected completion against date_required in shop local time.

    projected_completion is stored UTC; convert to Asia/Manila before taking
    .date(). Using the UTC calendar date would mis-flag jobs that finish
    after 16:00 UTC (00:00–07:59 the next day in Manila).

    Flag is not a separate material check — it only compares due date to the
    projected completion produced by placement. Material readiness affects the
    flag only by delaying that projection through not_before.
    """
    if not projected_completion_utc or not due_date:
        return None
    completion_date = utc_to_shop(projected_completion_utc).date()
    if completion_date <= due_date:
        return "GREEN"
    if completion_date <= due_date + timedelta(days=1):
        return "AMBER"
    return "RED"


def resolve_material_not_before_utc(
    material_status,
    material_received_date=None,
    material_expected_date=None,
) -> datetime | None:
    """
    Earliest UTC instant the first operation may start given material readiness.

    NOT_REQUIRED → unconstrained (None).
    Otherwise use received date when set, else expected date, at shop-local midnight
    so place_duration can clamp to the first working segment that day.
    """
    status_val = (
        material_status.value
        if hasattr(material_status, "value")
        else (material_status or "NOT_REQUIRED")
    )
    if status_val == "NOT_REQUIRED":
        return None
    ready = material_received_date or material_expected_date
    if ready is None:
        return None
    if isinstance(ready, str):
        ready = date.fromisoformat(ready[:10])
    return shop_local_to_utc(ready, time(0, 0))


def material_constraint_label(
    material_status,
    material_received_date=None,
    material_expected_date=None,
) -> str | None:
    """Human-readable reason for the material earliest-start line in planning UI."""
    status_val = (
        material_status.value
        if hasattr(material_status, "value")
        else (material_status or "NOT_REQUIRED")
    )
    if status_val == "NOT_REQUIRED":
        return None
    if material_received_date:
        d = material_received_date
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        return f"material received {d.isoformat()}"
    if material_expected_date:
        d = material_expected_date
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        return f"material expected {d.isoformat()}"
    return None


def _parse_estimated_hours(value) -> tuple[Decimal, bool]:
    if value is None or value == "":
        return DEFAULT_ESTIMATED_HOURS, True
    return Decimal(str(value)), False


def expected_finish(op: JobOperation, now_utc=None) -> datetime | None:
    """When in-progress work can be done: now plus its remaining target hours
    (target less hours worked, at least one hour) in the crew's working time."""
    from app.services.completion_estimate_service import hours_worked_so_far

    now = ensure_utc(now_utc or datetime.now(timezone.utc))
    target, _ = _parse_estimated_hours(op.estimated_hours)
    remaining = max(float(target) - hours_worked_so_far(op, now), MIN_REMAINING_HOURS)
    _start, end, _segments = place_from_start(op.crew_ids, now, remaining)
    return end


def paused_at(op: JobOperation) -> datetime | None:
    """When an in-progress operation was paused, while its last clock event is a pause."""
    from app.models.operation_time import OperationTimeEvent

    if op.status != OperationStatus.IN_PROGRESS or not op.time_logs:
        return None
    last = max(
        op.time_logs,
        key=lambda L: (ensure_utc(L.event_at), ensure_utc(L.created_at or L.event_at)),
    )
    return ensure_utc(last.event_at) if last.event == OperationTimeEvent.PAUSE else None


def stalled_since(op: JobOperation, now_utc=None) -> datetime | None:
    """When a paused operation became stalled: STALL_AFTER_WORKING_HOURS of its
    crew's working time after the pause with no resume. None while running,
    not paused, or paused for less than that."""
    paused = paused_at(op)
    if paused is None:
        return None
    _start, stall, _segments = place_from_start(op.crew_ids, paused, STALL_AFTER_WORKING_HOURS)
    now = ensure_utc(now_utc or datetime.now(timezone.utc))
    return stall if stall is not None and stall <= now else None


def _operation_booking_envelope(op: JobOperation, now_utc=None) -> tuple[datetime, datetime] | None:
    if op.status == OperationStatus.COMPLETED:
        if op.actual_start and op.actual_end:
            return ensure_utc(op.actual_start), ensure_utc(op.actual_end)
        if op.scheduled_start and op.scheduled_end:
            return ensure_utc(op.scheduled_start), ensure_utc(op.scheduled_end)
        return None
    if op.status == OperationStatus.IN_PROGRESS and (op.actual_start or op.scheduled_start):
        start = ensure_utc(op.actual_start or op.scheduled_start)
        ends = [ensure_utc(op.scheduled_end)] if op.scheduled_end else []
        now = ensure_utc(now_utc or datetime.now(timezone.utc))
        # A stalled operation's booking is worked out from when it stalled, so it
        # stops growing until someone resumes, re-plans or reassigns it.
        finish = expected_finish(op, stalled_since(op, now) or now)
        if finish is not None:
            ends.append(finish)
        end = max(ends, default=None)
        return (start, end) if end and end > start else None
    if op.actual_start and op.scheduled_end:
        return ensure_utc(op.actual_start), ensure_utc(op.scheduled_end)
    if op.scheduled_start and op.scheduled_end:
        return ensure_utc(op.scheduled_start), ensure_utc(op.scheduled_end)
    return None


def operation_working_segments(op: JobOperation, now_utc=None) -> list[tuple[datetime, datetime]]:
    """Public alias: derived working segments for one operation (never overnight gaps)."""
    return _busy_intervals_for_operation(op, now_utc)


def clocked_spans(op: JobOperation, now_utc=None) -> list[tuple[datetime, datetime]]:
    """Clocked START/RESUME -> PAUSE/COMPLETE spans; a running one up to now."""
    from app.models.operation_time import OperationTimeEvent

    now = ensure_utc(now_utc or datetime.now(timezone.utc))
    spans, open_at = [], None
    for log in sorted(op.time_logs or [], key=lambda L: ensure_utc(L.event_at)):
        at = ensure_utc(log.event_at)
        if log.event in (OperationTimeEvent.START, OperationTimeEvent.RESUME):
            open_at = open_at or at
        elif log.event in (OperationTimeEvent.PAUSE, OperationTimeEvent.COMPLETE):
            if open_at and at > open_at:
                spans.append((open_at, min(at, now)))
            open_at = None
    if open_at and now > open_at:
        spans.append((open_at, now))
    return [(s, e) for s, e in spans if e > s]


def _busy_intervals_for_operation(op: JobOperation, now_utc=None) -> list[tuple[datetime, datetime]]:
    """Worker/machine busy pieces for one op — derived segments, never overnight gaps.
    Work in progress is busy when it was clocked, then from now to its expected
    finish; time it sat paused is free for other work."""
    envelope = _operation_booking_envelope(op, now_utc)
    if not envelope:
        return []
    start, end = envelope
    if op.status == OperationStatus.IN_PROGRESS and op.time_logs:
        now = ensure_utc(now_utc or datetime.now(timezone.utc))
        worked = clocked_spans(op, now)
        ahead_from = max(start, now)
        ahead = []
        if end > ahead_from:
            ahead = (
                _segments_for_worker_envelope(op.crew_ids, ahead_from, end)
                if op.assigned_worker_id
                else [(ahead_from, end)]
            )
        return merge_intervals(worked + ahead)
    if not op.assigned_worker_id:
        return [(start, end)]
    schedule_by_dow = load_crew_schedule_map(op.crew_ids)
    exceptions = load_calendar_exceptions(
        utc_to_shop(start).date(), utc_to_shop(end).date()
    )
    return derive_working_segments(start, end, schedule_by_dow, exceptions)


def _crew(op: dict) -> list:
    """Lead first, then helpers, for a normalized operation dict."""
    lead = op.get("assignedWorkerId")
    if not lead:
        return []
    return [lead, *[h for h in (op.get("helperIds") or []) if h and h != lead]]


def _segments_for_worker_envelope(
    worker_ids,
    start: datetime,
    end: datetime,
    exceptions_by_date=None,
) -> list[tuple[datetime, datetime]]:
    if isinstance(worker_ids, str):
        worker_ids = [worker_ids]
    schedule_by_dow = load_crew_schedule_map(worker_ids)
    if exceptions_by_date is None:
        exceptions_by_date = load_calendar_exceptions(
            utc_to_shop(start).date(), utc_to_shop(end).date()
        )
    return derive_working_segments(start, end, schedule_by_dow, exceptions_by_date)


def _external_booking_records(exclude_job_id=None, exclude_operation_ids=None):
    """(operation, working periods) for every booking outside the given job."""
    from app.models.job_order import JobOrder, JobOrderStatus

    query = JobOperation.query.join(JobOrder, JobOperation.job_order_id == JobOrder.id).filter(
        JobOperation.status.in_(BOOKING_STATUSES),
        JobOrder.status.notin_((JobOrderStatus.DRAFT, JobOrderStatus.CANCELLED)),
    )
    if exclude_job_id:
        query = query.filter(JobOperation.job_order_id != exclude_job_id)
    exclude_operation_ids = set(exclude_operation_ids or [])
    records = []
    for op in query.all():
        if op.id in exclude_operation_ids:
            continue
        intervals = _busy_intervals_for_operation(op)
        if intervals:
            records.append((op, intervals))
    return records


def _load_external_bookings(exclude_job_id=None, exclude_operation_ids=None):
    worker_busy = {}
    machine_busy = {}
    for op, intervals in _external_booking_records(exclude_job_id, exclude_operation_ids):
        for wid in op.crew_ids:
            worker_busy.setdefault(wid, []).extend(intervals)
        if op.machine_unit_id:
            machine_busy.setdefault(op.machine_unit_id, []).extend(intervals)
            machine_busy.setdefault(str(op.machine_unit_id), []).extend(intervals)

    # Open machine downtimes block units for scheduling
    from app.services.operation_service import open_downtime_intervals_by_unit

    for unit_id, intervals in open_downtime_intervals_by_unit().items():
        machine_busy.setdefault(unit_id, []).extend(intervals)

    # A worker marked away (absent, on leave, emergency) cannot be booked then.
    from app.services.worker_cover_service import absence_intervals

    for worker_id, intervals in absence_intervals().items():
        worker_busy.setdefault(worker_id, []).extend(intervals)

    return worker_busy, machine_busy


def _open_downtimes():
    from app.services.operation_service import open_downtimes_by_unit

    return open_downtimes_by_unit()


def _breakdown_notes(units, downtimes) -> tuple[list[str], bool]:
    """Plain notes for candidate units that are down, and whether every unit is
    down with no usable expected repair date (so nothing can be placed)."""
    from app.services.operation_service import downtime_blocked_until

    notes = []
    indefinite = 0
    for unit in units:
        row = downtimes.get(unit.id)
        if row is None:
            continue
        until = downtime_blocked_until(row)
        if until is not None:
            notes.append(
                f"{unit.label} is down until {row.expected_repair_date.strftime('%a %d %b')}"
            )
            continue
        indefinite += 1
        if row.expected_repair_date:
            notes.append(
                f"{unit.label} is down and its expected repair date, "
                f"{row.expected_repair_date.strftime('%a %d %b')}, has passed"
            )
        else:
            notes.append(f"{unit.label} is down with no expected repair date")
    return notes, bool(units) and indefinite == len(units)


def _machine_units_by_type():
    grouped = {}
    for unit in MachineUnit.query.filter_by(active=True).order_by(MachineUnit.label).all():
        grouped.setdefault(unit.machine_type_id, []).append(unit)
    return grouped


def _outsourced_type(operation_type_id):
    """(is outsourced, default turnaround days) for an operation type id."""
    from app.models.worker_skill import OperationType

    if not operation_type_id:
        return False, None
    ot = OperationType.query.get(operation_type_id)
    if ot is None or not ot.is_outsourced:
        return False, None
    return True, ot.default_turnaround_days


def _turnaround(value, default):
    try:
        days = int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        days = None
    return days or default or 1


def _normalize_operation(op_data, seq_fallback: int) -> dict:
    if isinstance(op_data, JobOperation):
        est, defaulted = _parse_estimated_hours(op_data.estimated_hours)
        outsourced = op_data.is_outsourced
        default_days = (
            op_data.operation_type.default_turnaround_days if outsourced else None
        )
        envelope = (
            _operation_booking_envelope(op_data)
            if op_data.status == OperationStatus.IN_PROGRESS
            else None
        )
        return {
            # In-progress work stays booked until its projected finish.
            "bookedEnd": envelope[1].isoformat() if envelope else None,
            "id": op_data.id,
            "sequenceNo": op_data.sequence_no,
            "operationName": op_data.operation_name,
            "operationTypeId": op_data.operation_type_id,
            "machineTypeId": op_data.machine_type_id,
            "machineUnitId": op_data.machine_unit_id,
            "assignedWorkerId": op_data.assigned_worker_id,
            "helperIds": op_data.helper_ids,
            "estimatedHours": float(est),
            "estimatedHoursDefaulted": defaulted,
            "status": op_data.status.value if op_data.status else "PENDING",
            "scheduledStart": op_data.scheduled_start.isoformat() if op_data.scheduled_start else None,
            "scheduledEnd": op_data.scheduled_end.isoformat() if op_data.scheduled_end else None,
            "actualStart": op_data.actual_start.isoformat() if op_data.actual_start else None,
            "actualEnd": op_data.actual_end.isoformat() if op_data.actual_end else None,
            "outsourced": outsourced,
            "turnaroundDays": (
                _turnaround(op_data.turnaround_days, default_days) if outsourced else None
            ),
        }
    est, defaulted = _parse_estimated_hours(op_data.get("estimatedHours"))
    outsourced, default_days = _outsourced_type(op_data.get("operationTypeId"))
    if not op_data.get("operationTypeId") and (op_data.get("isOutsourced") or op_data.get("outsourced")):
        outsourced = True
    return {
        "id": op_data.get("id"),
        "sequenceNo": int(op_data.get("sequenceNo", op_data.get("seq", seq_fallback))),
        "operationName": op_data.get("operationName") or op_data.get("name") or "",
        "operationTypeId": op_data.get("operationTypeId"),
        "machineTypeId": op_data.get("machineTypeId"),
        "machineUnitId": op_data.get("machineUnitId"),
        "assignedWorkerId": op_data.get("assignedWorkerId"),
        "helperIds": [h for h in (op_data.get("helperIds") or []) if h],
        "estimatedHours": float(est),
        "estimatedHoursDefaulted": defaulted,
        "status": op_data.get("status", "PENDING"),
        "scheduledStart": op_data.get("scheduledStart"),
        "scheduledEnd": op_data.get("scheduledEnd"),
        "actualStart": op_data.get("actualStart"),
        "actualEnd": op_data.get("actualEnd"),
        "outsourced": outsourced,
        "turnaroundDays": (
            _turnaround(op_data.get("turnaroundDays"), default_days) if outsourced else None
        ),
    }


def _outsourced_window(op: dict, start: datetime, message=None) -> dict:
    """Away from the shop for its turnaround in calendar days; no worker or machine."""
    end = start + timedelta(days=int(op["turnaroundDays"] or 1))
    return _result_from_slot(
        {**op, "assignedWorkerId": None, "helperIds": []},
        start.isoformat(),
        end.isoformat(),
        None,
        scheduled=True,
        message=message,
    )


def _frozen_result(op: dict) -> dict | None:
    status = op.get("status")
    if status == OperationStatus.COMPLETED.value:
        start = op.get("actualStart") or op.get("scheduledStart")
        end = op.get("actualEnd") or op.get("scheduledEnd")
        if start and end:
            return _result_from_slot(
                op,
                start,
                end,
                op.get("machineUnitId"),
                scheduled=True,
                message=None,
                machine_unit_label=op.get("machineUnitLabel"),
            )
    started = status == OperationStatus.IN_PROGRESS.value or bool(op.get("actualStart"))
    if started and op.get("scheduledEnd"):
        start = op.get("actualStart") or op.get("scheduledStart")
        if start:
            return _result_from_slot(
                op,
                start,
                op["scheduledEnd"],
                op.get("machineUnitId"),
                scheduled=True,
                message="in progress — existing window kept",
                machine_unit_label=op.get("machineUnitLabel"),
            )
    return None


def _unit_by_id(unit_id, units_by_type):
    if not unit_id:
        return None
    for units in units_by_type.values():
        for unit in units:
            if str(unit.id) == str(unit_id):
                return unit
    return None


def _unit_label(unit_id, units_by_type):
    unit = _unit_by_id(unit_id, units_by_type)
    return unit.label if unit is not None else None


def _result_from_slot(
    op,
    start,
    end,
    machine_unit_id,
    *,
    scheduled: bool,
    message: str | None,
    placeable_hours: float | None = None,
    required_hours: float | None = None,
    machine_unit_label: str | None = None,
    exceptions_by_date=None,
):
    start_dt = ensure_utc(datetime.fromisoformat(str(start).replace("Z", "+00:00")))
    end_dt = ensure_utc(datetime.fromisoformat(str(end).replace("Z", "+00:00")))
    wid = op.get("assignedWorkerId")
    if op.get("outsourced"):
        segments = [(start_dt, end_dt)]
    elif wid:
        segments = _segments_for_worker_envelope(_crew(op), start_dt, end_dt, exceptions_by_date)
    else:
        segments = []
    return {
        "id": op.get("id"),
        "sequenceNo": op["sequenceNo"],
        "operationName": op.get("operationName"),
        "assignedWorkerId": wid,
        "helperIds": list(op.get("helperIds") or []),
        "machineTypeId": op.get("machineTypeId"),
        "machineUnitId": machine_unit_id,
        "machineUnitLabel": machine_unit_label,
        "estimatedHours": op["estimatedHours"],
        "estimatedHoursDefaulted": op["estimatedHoursDefaulted"],
        "scheduledStart": start_dt.isoformat(),
        "scheduledEnd": end_dt.isoformat(),
        "segments": serialize_segments(segments),
        "scheduled": scheduled,
        "message": message,
        "placeableHours": placeable_hours,
        "requiredHours": required_hours,
        "isOutsourced": bool(op.get("outsourced")),
        "turnaroundDays": op.get("turnaroundDays"),
    }


def _failure_result(op, message, *, placeable_hours=None, required_hours=None):
    return {
        "id": op.get("id"),
        "sequenceNo": op["sequenceNo"],
        "operationName": op.get("operationName"),
        "assignedWorkerId": op.get("assignedWorkerId"),
        "helperIds": list(op.get("helperIds") or []),
        "machineTypeId": op.get("machineTypeId"),
        "machineUnitId": None,
        "estimatedHours": op["estimatedHours"],
        "estimatedHoursDefaulted": op["estimatedHoursDefaulted"],
        "scheduledStart": None,
        "scheduledEnd": None,
        "segments": [],
        "scheduled": False,
        "message": message,
        "placeableHours": placeable_hours,
        "requiredHours": required_hours,
    }


def _worker_windows_and_busy(
    worker_id,
    anchor_utc,
    end_utc,
    worker_busy,
    in_job_busy,
    exceptions_by_date,
):
    """(shared working windows, booked periods) for a worker or a crew (lead
    first) within the horizon: the crew works when every member works, and is
    booked whenever any member is."""
    ids = [worker_id] if isinstance(worker_id, str) else [w for w in worker_id if w]
    schedule_by_dow = load_crew_schedule_map(ids)
    working = build_worker_working_windows(
        schedule_by_dow, exceptions_by_date, anchor_utc, end_utc
    )
    booked = []
    for wid in ids:
        booked += worker_busy.get(wid, []) + in_job_busy.get(wid, [])
    return working, merge_intervals(booked)


def _busy_seconds_in_horizon(intervals, anchor_utc, end_utc) -> float:
    total = 0.0
    for s, e in merge_intervals(intervals or []):
        s = max(ensure_utc(s), anchor_utc)
        e = min(ensure_utc(e), end_utc)
        if e > s:
            total += (e - s).total_seconds()
    return total


def _crew_label(op: dict) -> str:
    """'Ana' or 'The workers (Ana, Ben)' for messages."""
    crew = _crew(op)
    if len(crew) <= 1:
        return _worker_label(crew[0]) if crew else "The assigned worker"
    return f"The workers ({', '.join(_worker_label(w) for w in crew)})"


def _worker_label(worker_id) -> str:
    from app.extensions import db
    from app.models.user import User

    try:
        user = db.session.get(User, worker_id)
    except Exception:
        user = None
    return user.full_name if user else "The assigned worker"


def no_hours_messages(worker_ids) -> list[str]:
    """'<Name> has no working hours set; …' for each given worker without hours."""
    ids = [w for w in (worker_ids or []) if w]
    # A crew's shared map is empty only when someone in it has no hours.
    if not ids or load_crew_schedule_map(ids):
        return []
    return [
        f"{_worker_label(w)} has no working hours set; set them on Worker setup"
        for w in ids
        if not has_working_hours(load_crew_schedule_map([w]))
    ]


OWN_UNIT, OPEN_UNIT, OTHERS_UNIT = 0, 1, 2


def unit_ownership(unit, lead_id) -> int:
    """OWN_UNIT when the lead is the unit's assigned operator, OPEN_UNIT when it
    has none, OTHERS_UNIT when it is another operator's."""
    owner = getattr(unit, "default_operator_id", None)
    if not owner:
        return OPEN_UNIT
    return OWN_UNIT if lead_id and str(owner) == str(lead_id) else OTHERS_UNIT


def others_unit_note(unit, lead_id) -> str | None:
    """'Lathe #4 is Ben Santos's unit' when the lead would use another operator's unit."""
    if unit is None or unit_ownership(unit, lead_id) != OTHERS_UNIT:
        return None
    return f"{unit.label} is {_unit_owner_name(unit)}'s unit"


def _unit_owner_name(unit) -> str:
    owner = unit.default_operator
    return owner.full_name if owner else "another operator"


def _find_earliest_slot(
    worker_ids,
    machine_type_id,
    duration: timedelta,
    not_before: datetime,
    anchor_utc,
    end_utc,
    worker_busy,
    machine_busy,
    in_job_worker_busy,
    in_job_machine_busy,
    exceptions_by_date,
    units_by_type,
    preferred_unit_id=None,
    preferred_worker_id=None,
    helper_ids=None,
):
    """
    Earliest feasible (worker, unit) assignment. ``worker_ids`` are candidate
    leads; ``helper_ids`` join every candidate, so the slot is free for the
    whole crew (and inside the hours they all work).
    Rank: earliest start → the lead's own unit, then a unit with no assigned
    operator, then another operator's unit → least occupied machine → keep
    preferred worker → label.
    """
    if isinstance(worker_ids, str):
        worker_ids = [worker_ids]
    worker_ids = [w for w in (worker_ids or []) if w]
    if not worker_ids:
        return None, None, None, None, 0.0
    helper_ids = [h for h in (helper_ids or []) if h]

    def crew_of(wid):
        return [wid, *[h for h in helper_ids if h != wid]]

    if not machine_type_id:
        best = None
        max_placeable = 0.0
        for wid in worker_ids:
            working, busy = _worker_windows_and_busy(
                crew_of(wid),
                anchor_utc,
                end_utc,
                worker_busy,
                in_job_worker_busy,
                exceptions_by_date,
            )
            start, end, placeable = place_unbroken(
                working, busy, duration, not_before, end_utc
            )
            max_placeable = max(max_placeable, placeable)
            if not start or not end:
                continue
            keep_pref = 0 if preferred_worker_id and wid == preferred_worker_id else 1
            candidate = (start, keep_pref, wid, end, placeable)
            if best is None or candidate[:2] < best[:2] or (
                candidate[:2] == best[:2] and candidate[2] < best[2]
            ):
                best = candidate
        if best:
            start, _k, wid, end, placeable = best
            return start, end, None, wid, placeable
        return None, None, None, None, max_placeable

    units = list(units_by_type.get(machine_type_id, []))
    if preferred_unit_id:
        pref = str(preferred_unit_id)
        units = [u for u in units if str(u.id) == pref]
    if not units:
        return None, None, None, None, 0.0

    # (start, ownership, busy_secs, keep_preferred_worker, label, ...)
    best = None
    max_placeable = 0.0

    for wid in worker_ids:
        working, worker_booked = _worker_windows_and_busy(
            crew_of(wid),
            anchor_utc,
            end_utc,
            worker_busy,
            in_job_worker_busy,
            exceptions_by_date,
        )
        for unit in units:
            unit_busy = merge_intervals(
                machine_busy.get(unit.id, [])
                + machine_busy.get(str(unit.id), [])
                + in_job_machine_busy.get(unit.id, [])
                + in_job_machine_busy.get(str(unit.id), [])
            )
            start, end, placeable = place_unbroken(
                working, worker_booked + unit_busy, duration, not_before, end_utc
            )
            max_placeable = max(max_placeable, placeable)
            if not start or not end:
                continue
            busy_secs = _busy_seconds_in_horizon(unit_busy, anchor_utc, end_utc)
            keep_pref = 0 if preferred_worker_id and wid == preferred_worker_id else 1
            candidate = (
                start,
                unit_ownership(unit, wid),
                busy_secs,
                keep_pref,
                unit.label or "",
                end,
                unit.id,
                wid,
                placeable,
            )
            if best is None or candidate[:5] < best[:5]:
                best = candidate

    if best:
        start, _b, _d, _k, _lab, end, unit_id, wid, placeable = best
        return start, end, unit_id, wid, placeable
    return None, None, None, None, max_placeable


def next_free_for_pairs(pairs, hours=None, *, exclude_job_id=None, anchor_utc=None):
    """{(worker id, unit id or None): (start, end)} — the earliest time both the
    worker and the unit are free for ``hours`` of work, given released jobs,
    machine downtime and the work calendar. (None, None) when nothing fits in
    the horizon."""
    anchor = ensure_utc(anchor_utc or shop_now().astimezone(timezone.utc))
    end_utc = horizon_end_utc(anchor)
    worker_busy, machine_busy = _load_external_bookings(exclude_job_id=exclude_job_id)
    exceptions = load_calendar_exceptions(utc_to_shop(anchor).date(), utc_to_shop(end_utc).date())
    est, _ = _parse_estimated_hours(hours if hours not in (0, "0") else None)
    duration = timedelta(hours=float(est))
    per_worker = {}
    out = {}
    for wid, uid in pairs:
        if wid not in per_worker:
            per_worker[wid] = _worker_windows_and_busy(
                [wid], anchor, end_utc, worker_busy, {}, exceptions
            )
        working, booked = per_worker[wid]
        unit_busy = (
            machine_busy.get(uid, []) + machine_busy.get(str(uid), []) if uid else []
        )
        start, end, _placeable = place_unbroken(
            working, merge_intervals(booked + unit_busy), duration, anchor, end_utc
        )
        out[(wid, uid)] = (start, end)
    return out


def _alternative_pairs(op: dict, units_by_type):
    """(worker id, unit id or None) pairs that could lead ``op`` instead of its
    current pair: people qualified for its machine (anyone assignable when the
    machine has no skills recorded, or for work without a machine), each on
    their own unit or an open one."""
    from app.services.worker_profile_service import machine_skill_holders, query_assignable_workers

    machine_type_id = op.get("machineTypeId")
    workers = query_assignable_workers().all()
    if machine_type_id:
        holders = machine_skill_holders(machine_type_id)
        if holders is not None:
            workers = [w for w in workers if w.id in holders]
    current = (op.get("assignedWorkerId"), op.get("machineUnitId") or None)
    pairs = []
    for w in workers:
        if not machine_type_id:
            if w.id != current[0]:
                pairs.append((w.id, None))
            continue
        for unit in units_by_type.get(machine_type_id, []):
            if (w.id, unit.id) != current and (w.id, str(unit.id)) != current:
                pairs.append((w.id, unit.id))
    return pairs


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    return ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _fmt_shop(dt: datetime) -> str:
    return utc_to_shop(dt).strftime("%a %b %d, %H:%M")


def place_from_start(worker_ids, start, hours):
    """
    Working time for an operation starting at ``start``, or at the crew's next
    shared working time after it, and running ``hours`` across working hours,
    overtime and holidays. ``worker_ids`` is one worker id or the crew, lead
    first. Returns (start, end, segments); (None, None, []) when there is no
    such time within the horizon.
    """
    start = ensure_utc(start)
    horizon = horizon_end_utc(start)
    if isinstance(worker_ids, str):
        worker_ids = [worker_ids]
    schedule_by_dow = load_crew_schedule_map(worker_ids)
    exceptions = load_calendar_exceptions(utc_to_shop(start).date(), utc_to_shop(horizon).date())
    windows = build_worker_working_windows(schedule_by_dow, exceptions, start, horizon)
    placed_start, placed_end, _ = place_duration(
        windows, timedelta(hours=float(hours)), start, horizon
    )
    if placed_start is None:
        return None, None, []
    return placed_start, placed_end, intersect_intervals([(placed_start, placed_end)], windows)


def _window_from_start(op: dict, requested_start, choose_unit=None) -> dict:
    """The operation starts at the requested time (moved to the next working time
    when outside working hours) and ends once its target hours are worked.
    ``choose_unit(op, segments)`` returns (unit id, label, note) for a machine
    operation; without it the operation keeps its unit."""
    if op.get("outsourced"):
        return _outsourced_window(op, _parse_iso(requested_start))
    if not op.get("assignedWorkerId"):
        return _failure_result(op, MISSING_WORKER_MESSAGE, required_hours=op["estimatedHours"])
    no_hours = no_hours_messages(_crew(op))
    if no_hours:
        return _failure_result(op, "; ".join(no_hours), required_hours=op["estimatedHours"])
    requested = _parse_iso(requested_start)
    start, end, segments = place_from_start(_crew(op), requested, op["estimatedHours"])
    if start is None:
        return _failure_result(
            op,
            f"{_crew_label(op)} has no working time for "
            f"{op['estimatedHours']:.1f}h within {SCHEDULE_HORIZON_DAYS} days of the chosen start",
            required_hours=op["estimatedHours"],
        )
    notes = []
    if start != requested:
        notes.append(f"Start moved to the next working time, {_fmt_shop(start)}")
    unit_id, unit_label = op.get("machineUnitId"), op.get("machineUnitLabel")
    if choose_unit and op.get("machineTypeId"):
        unit_id, unit_label, note = choose_unit(op, segments)
        if note:
            notes.append(note)
    return _result_from_slot(
        op,
        start.isoformat(),
        end.isoformat(),
        unit_id,
        scheduled=True,
        message="; ".join(notes) or None,
        machine_unit_label=unit_label,
    )


def _overlaps_any(segments, intervals) -> bool:
    return any(s < ie and i_s < e for s, e in segments for i_s, ie in intervals)


def _free_unit_chooser(
    units_by_type, machine_busy, in_job_machine_busy, anchor_utc, end_utc, honor_machine_pins
):
    """Unit choice for an operation placed at a typed start, ranked as the normal
    search does: the lead's own unit, then an open unit, then another
    operator's; then least booked in the horizon, then label. The current unit
    stays while it is free (or pinned); with no free unit the operation keeps
    its unit, or has none. Choosing another operator's unit adds a note."""

    def unit_busy(unit_id):
        return merge_intervals(
            machine_busy.get(unit_id, [])
            + machine_busy.get(str(unit_id), [])
            + in_job_machine_busy.get(unit_id, [])
            + in_job_machine_busy.get(str(unit_id), [])
        )

    def choose(op, segments):
        units = list(units_by_type.get(op.get("machineTypeId"), []))
        current = op.get("machineUnitId")
        if current and honor_machine_pins:
            return current, _unit_label(current, units_by_type), None
        free = [u for u in units if not _overlaps_any(segments, unit_busy(u.id))]
        if current and any(str(u.id) == str(current) for u in free):
            return current, _unit_label(current, units_by_type), None
        if not free:
            if current:
                return current, _unit_label(current, units_by_type), None
            return None, None, "no machine unit is free at this time"
        lead = op.get("assignedWorkerId")
        best = min(
            free,
            key=lambda u: (
                unit_ownership(u, lead),
                _busy_seconds_in_horizon(unit_busy(u.id), anchor_utc, end_utc),
                u.label or "",
            ),
        )
        return best.id, best.label, others_unit_note(best, lead)

    return choose


def _lock_existing_window(op: dict, choose_unit=None) -> dict | None:
    """Keep a previously proposed start when partially re-proposing; the end is
    always worked out again from the target hours."""
    if not op.get("scheduledStart"):
        return None
    return _window_from_start(op, op["scheduledStart"], choose_unit)


def _record_in_job_busy(op_result, op, in_job_worker_busy, in_job_machine_busy):
    crew = _crew(op_result) or _crew(op)
    uid = op_result.get("machineUnitId")
    segments = [
        (
            ensure_utc(datetime.fromisoformat(s["start"].replace("Z", "+00:00"))),
            ensure_utc(datetime.fromisoformat(s["end"].replace("Z", "+00:00"))),
        )
        for s in (op_result.get("segments") or [])
    ]
    for wid in crew if segments else []:
        in_job_worker_busy.setdefault(wid, []).extend(segments)
    if uid and segments:
        in_job_machine_busy.setdefault(uid, []).extend(segments)
        in_job_machine_busy.setdefault(str(uid), []).extend(segments)


def _due_end_utc(due_date) -> datetime:
    if isinstance(due_date, str):
        due_date = date.fromisoformat(due_date[:10])
    return shop_local_to_utc(due_date + timedelta(days=1), time(0, 0))


def _suggest_faster_pairs(
    normalized,
    results,
    projected,
    due_date,
    not_before_by_seq,
    units_by_type,
    exceptions_by_date,
    worker_busy,
    machine_busy,
    anchor_utc,
    end_utc,
    *,
    alt_kwargs,
):
    """For each operation (up to FASTER_PAIR_OPS) that ends after the date
    required, set ``fasterPair``: the worker + unit that makes the whole job
    finish earliest, at least FASTER_UNIT_MIN_GAIN sooner. Never applied."""
    due_end = _due_end_utc(due_date)
    if projected <= due_end:
        return
    by_seq = {r["sequenceNo"]: r for r in results}
    late = [
        op
        for op in normalized
        if op["sequenceNo"] in not_before_by_seq
        and by_seq.get(op["sequenceNo"], {}).get("scheduled")
        and _parse_iso(by_seq[op["sequenceNo"]]["scheduledEnd"]) > due_end
    ][:FASTER_PAIR_OPS]
    from app.extensions import db
    from app.models.user import User

    for op in late:
        row = by_seq[op["sequenceNo"]]
        current_end = _parse_iso(row["scheduledEnd"])
        duration = timedelta(hours=float(op["estimatedHours"]))
        quick = []
        for wid, uid in _alternative_pairs(op, units_by_type):
            helpers = [h for h in (op.get("helperIds") or []) if h != wid]
            start, end, unit_id, _w, _p = _find_earliest_slot(
                [wid],
                op.get("machineTypeId"),
                duration,
                not_before_by_seq[op["sequenceNo"]],
                anchor_utc,
                end_utc,
                worker_busy,
                machine_busy,
                {},
                {},
                exceptions_by_date,
                units_by_type,
                preferred_unit_id=uid,
                preferred_worker_id=wid,
                helper_ids=helpers,
            )
            if start and end and end < current_end:
                quick.append((end, wid, unit_id, helpers))
        quick.sort(key=lambda q: (q[0], str(q[1])))
        best = None
        for _end, wid, unit_id, helpers in quick[:FASTER_PAIR_TRIES]:
            swapped = [
                {**o, "assignedWorkerId": wid, "machineUnitId": unit_id, "helperIds": helpers}
                if o is op
                else o
                for o in normalized
            ]
            alt = propose_schedule(
                swapped,
                due_date,
                honor_machine_pins=True,
                suggest_faster_units=False,
                _bookings=(worker_busy, machine_busy),
                **alt_kwargs,
            )
            if not all(r.get("scheduled") for r in alt["operations"]):
                continue
            finish = _parse_iso(alt["projectedCompletion"])
            if finish is None or projected - finish < FASTER_UNIT_MIN_GAIN:
                continue
            if best is None or finish < best[0]:
                best = (finish, wid, unit_id, alt)
        if best is None:
            continue
        finish, wid, unit_id, alt = best
        placed = next(r for r in alt["operations"] if r["sequenceNo"] == op["sequenceNo"])
        worker = db.session.get(User, wid)
        unit = _unit_by_id(unit_id, units_by_type)
        row["fasterPair"] = {
            "assignedWorkerId": wid,
            "workerName": worker.full_name if worker else None,
            "workerNickname": worker.nickname if worker else None,
            "machineUnitId": unit_id,
            "machineUnitLabel": unit.label if unit is not None else None,
            "scheduledStart": placed["scheduledStart"],
            "scheduledEnd": placed["scheduledEnd"],
            "projectedCompletion": alt["projectedCompletion"],
            "currentProjectedCompletion": projected.isoformat(),
            "onTime": finish <= due_end,
        }
        row.pop("fasterUnit", None)


def propose_schedule(
    operations,
    due_date,
    *,
    exclude_job_id=None,
    anchor_utc=None,
    lock_before_sequence=None,
    honor_machine_pins=False,
    material_not_before_utc=None,
    material_constraint_reason=None,
    never_earlier=False,
    pin_sequence=None,
    suggest_faster_units=True,
    _bookings=None,
):
    """
    Earliest-fit proposal for a job's operations. Does not write to the database.

    lock_before_sequence: keep scheduled starts for ops with sequenceNo < this
      (used when re-fitting after a machine/time edit); ends are worked out again.
    honor_machine_pins: place each op on its machineUnitId when set (partial re-fit).
    material_not_before_utc: raises the first-op not_before floor (material readiness).
    never_earlier: no op is placed before its current scheduled start.
    pin_sequence: this op starts at its scheduledStart (moved to the next working
      time if needed) whatever else is booked; clashes are reported by
      schedule_problems. Ops after it are re-placed from its end.
    suggest_faster_units: with pins honoured, an op whose pinned unit makes the
      job finish at least FASTER_UNIT_MIN_GAIN later than another unit of the
      same type (or cannot be placed at all) gets ``fasterUnit``. The pinned
      unit is never swapped. When the job finishes after its date required, an
      operation that ends late may instead get ``fasterPair``: another worker +
      unit that finishes the job earlier.
    """
    anchor_utc = ensure_utc(anchor_utc or shop_now().astimezone(timezone.utc))
    end_utc = horizon_end_utc(anchor_utc)

    def _seq_key(item):
        if isinstance(item, JobOperation):
            return item.sequence_no
        return int(item.get("sequenceNo", item.get("seq", 0)))

    raw_sorted = sorted(operations, key=_seq_key)
    normalized = [
        _normalize_operation(op, i) for i, op in enumerate(raw_sorted, start=1)
    ]
    normalized.sort(key=lambda o: o["sequenceNo"])

    exclude_op_ids = [o["id"] for o in normalized if o.get("id")]
    worker_busy, machine_busy = _bookings or _load_external_bookings(
        exclude_job_id=exclude_job_id,
        exclude_operation_ids=exclude_op_ids,
    )
    units_by_type = _machine_units_by_type()
    faster_candidates = []

    anchor_shop = utc_to_shop(anchor_utc)
    end_shop = utc_to_shop(end_utc)
    exceptions_by_date = load_calendar_exceptions(anchor_shop.date(), end_shop.date())

    in_job_worker_busy = {}
    in_job_machine_busy = {}
    choose_unit = _free_unit_chooser(
        units_by_type, machine_busy, in_job_machine_busy, anchor_utc, end_utc, honor_machine_pins
    )
    downtimes = None
    results = []
    not_before_by_seq = {}
    prev_end = anchor_utc
    if material_not_before_utc is not None:
        prev_end = max(prev_end, ensure_utc(material_not_before_utc))

    for op in normalized:
        frozen = _frozen_result(op)
        locked = None
        if (
            not frozen
            and lock_before_sequence is not None
            and op["sequenceNo"] < int(lock_before_sequence)
        ):
            locked = _lock_existing_window(op, choose_unit)
        if (
            not frozen
            and not locked
            and pin_sequence is not None
            and op["sequenceNo"] == int(pin_sequence)
            and op.get("scheduledStart")
        ):
            locked = _window_from_start(op, op["scheduledStart"], choose_unit)

        kept = frozen or locked
        if kept and not kept.get("scheduled"):
            results.append(kept)
            continue
        if kept:
            results.append(kept)
            frozen_end = ensure_utc(
                datetime.fromisoformat(kept["scheduledEnd"].replace("Z", "+00:00"))
            )
            _record_in_job_busy(kept, op, in_job_worker_busy, in_job_machine_busy)
            booked_end = _parse_iso(op.get("bookedEnd")) if frozen else None
            if booked_end and booked_end > frozen_end:
                overrun = {
                    **kept,
                    "segments": serialize_segments(
                        _segments_for_worker_envelope(
                            _crew(op), frozen_end, booked_end, exceptions_by_date
                        )
                        if _crew(op)
                        else [(frozen_end, booked_end)]
                    ),
                }
                _record_in_job_busy(overrun, op, in_job_worker_busy, in_job_machine_busy)
                frozen_end = booked_end
            prev_end = max(prev_end, frozen_end)
            continue

        if op.get("outsourced"):
            not_before = prev_end
            if never_earlier and op.get("scheduledStart"):
                not_before = max(not_before, _parse_iso(op["scheduledStart"]))
            slot = _outsourced_window(op, not_before)
            results.append(slot)
            prev_end = _parse_iso(slot["scheduledEnd"])
            continue

        # The Admin's worker choice is fixed; only the time moves.
        assigned_worker = op.get("assignedWorkerId")
        if not assigned_worker:
            results.append(
                _failure_result(op, MISSING_WORKER_MESSAGE, required_hours=op["estimatedHours"])
            )
            continue
        no_hours = no_hours_messages(_crew(op))
        if no_hours:
            results.append(
                _failure_result(op, "; ".join(no_hours), required_hours=op["estimatedHours"])
            )
            continue

        duration = timedelta(hours=float(op["estimatedHours"]))
        not_before = prev_end
        if never_earlier and op.get("scheduledStart"):
            current = op["scheduledStart"]
            if isinstance(current, str):
                current = datetime.fromisoformat(current.replace("Z", "+00:00"))
            not_before = max(not_before, ensure_utc(current))
        preferred_unit = op.get("machineUnitId") if honor_machine_pins else None
        not_before_by_seq[op["sequenceNo"]] = not_before

        start, end, unit_id, _worker, placeable = _find_earliest_slot(
            [assigned_worker],
            op.get("machineTypeId"),
            duration,
            not_before,
            anchor_utc,
            end_utc,
            worker_busy,
            machine_busy,
            in_job_worker_busy,
            in_job_machine_busy,
            exceptions_by_date,
            units_by_type,
            preferred_unit_id=preferred_unit,
            preferred_worker_id=assigned_worker,
            helper_ids=op.get("helperIds"),
        )

        if suggest_faster_units and preferred_unit and op.get("machineTypeId"):
            a_start, a_end, a_unit, _w, _p = _find_earliest_slot(
                [assigned_worker],
                op.get("machineTypeId"),
                duration,
                not_before,
                anchor_utc,
                end_utc,
                worker_busy,
                machine_busy,
                in_job_worker_busy,
                in_job_machine_busy,
                exceptions_by_date,
                units_by_type,
                preferred_worker_id=assigned_worker,
                helper_ids=op.get("helperIds"),
            )
            if a_unit and str(a_unit) != str(preferred_unit) and (not end or a_end < end):
                faster_candidates.append((op, a_unit, a_start, a_end, start))

        required = float(op["estimatedHours"])
        if not start or not end:
            worker_label = _crew_label(op)
            candidates = list(units_by_type.get(op.get("machineTypeId"), []))
            if preferred_unit:
                candidates = [u for u in candidates if str(u.id) == str(preferred_unit)]
            if downtimes is None:
                downtimes = _open_downtimes()
            down_notes, all_down = _breakdown_notes(candidates, downtimes)
            if all_down:
                msg = "; ".join(down_notes)
            elif placeable <= 0 and op.get("machineTypeId") and not units_by_type.get(op["machineTypeId"]):
                msg = (
                    f"could not schedule within {SCHEDULE_HORIZON_DAYS} days "
                    f"(no machine units configured; {required:.1f}h required)"
                )
            elif preferred_unit and placeable <= 0:
                msg = (
                    f"could not schedule within {SCHEDULE_HORIZON_DAYS} days "
                    f"(selected machine unit unavailable; 0.0h placeable of {required:.1f}h required)"
                )
            elif op.get("machineTypeId") and placeable <= 0:
                msg = (
                    f"could not schedule within {SCHEDULE_HORIZON_DAYS} days "
                    f"(all machine units busy; 0.0h placeable of {required:.1f}h required)"
                )
            else:
                together = " together" if len(_crew(op)) > 1 else ""
                msg = (
                    f"could not schedule within {SCHEDULE_HORIZON_DAYS} days: "
                    f"{worker_label} has {placeable:.1f}h free{together} of {required:.1f}h required. "
                    "Assign another worker or free up their time"
                )
            if down_notes and not all_down:
                msg = f"{msg}. {'; '.join(down_notes)}"
            results.append(
                _failure_result(
                    op,
                    msg,
                    placeable_hours=round(placeable, 2),
                    required_hours=required,
                )
            )
            continue

        placed_op = {**op, "assignedWorkerId": assigned_worker}
        slot = _result_from_slot(
            placed_op,
            start.isoformat(),
            end.isoformat(),
            unit_id,
            scheduled=True,
            message=None,
            machine_unit_label=_unit_label(unit_id, units_by_type),
            exceptions_by_date=exceptions_by_date,
        )
        results.append(slot)
        prev_end = end
        _record_in_job_busy(slot, placed_op, in_job_worker_busy, in_job_machine_busy)

    scheduled_ends = [
        ensure_utc(datetime.fromisoformat(r["scheduledEnd"].replace("Z", "+00:00")))
        for r in results
        if r.get("scheduled") and r.get("scheduledEnd")
    ]
    projected = max(scheduled_ends) if scheduled_ends else None
    flag = compute_schedule_flag(projected, due_date)

    if faster_candidates:
        all_placed = all(r.get("scheduled") for r in results)
        by_seq = {r["sequenceNo"]: r for r in results}
        for op, unit_id, alt_start, alt_end, pinned_start in faster_candidates[:FASTER_UNIT_MAX]:
            swapped = [
                {**o, "machineUnitId": unit_id if o is op else o.get("machineUnitId")}
                for o in normalized
            ]
            alt = propose_schedule(
                swapped,
                due_date,
                exclude_job_id=exclude_job_id,
                anchor_utc=anchor_utc,
                lock_before_sequence=lock_before_sequence,
                honor_machine_pins=True,
                material_not_before_utc=material_not_before_utc,
                material_constraint_reason=material_constraint_reason,
                never_earlier=never_earlier,
                pin_sequence=pin_sequence,
                suggest_faster_units=False,
                _bookings=(worker_busy, machine_busy),
            )
            if not all(r.get("scheduled") for r in alt["operations"]):
                continue
            alt_finish = _parse_iso(alt["projectedCompletion"])
            if all_placed and (projected is None or projected - alt_finish < FASTER_UNIT_MIN_GAIN):
                continue
            unit = _unit_by_id(unit_id, units_by_type)
            lead = op.get("assignedWorkerId")
            hours_earlier = None
            if pinned_start:
                working, _busy = _worker_windows_and_busy(
                    _crew(op), anchor_utc, end_utc, {}, {}, exceptions_by_date
                )
                hours_earlier = round(
                    _busy_seconds_in_horizon(
                        intersect_intervals([(alt_start, pinned_start)], working),
                        anchor_utc,
                        end_utc,
                    )
                    / 3600,
                    1,
                )
            owner = unit.default_operator if unit is not None else None
            by_seq[op["sequenceNo"]]["fasterUnit"] = {
                "machineUnitId": unit_id,
                "machineUnitLabel": unit.label if unit is not None else None,
                "ownership": ("OWN", "OPEN", "OTHER")[unit_ownership(unit, lead)],
                "operatorName": owner.full_name if owner else None,
                "scheduledStart": alt_start.isoformat(),
                "scheduledEnd": alt_end.isoformat(),
                "hoursEarlier": hours_earlier,
                "projectedCompletion": alt["projectedCompletion"],
                "currentProjectedCompletion": (
                    projected.isoformat() if projected and all_placed else None
                ),
            }

    if suggest_faster_units and due_date and projected is not None:
        _suggest_faster_pairs(
            normalized,
            results,
            projected,
            due_date,
            not_before_by_seq,
            units_by_type,
            exceptions_by_date,
            worker_busy,
            machine_busy,
            anchor_utc,
            end_utc,
            alt_kwargs={
                "exclude_job_id": exclude_job_id,
                "anchor_utc": anchor_utc,
                "lock_before_sequence": lock_before_sequence,
                "material_not_before_utc": material_not_before_utc,
                "material_constraint_reason": material_constraint_reason,
                "never_earlier": never_earlier,
                "pin_sequence": pin_sequence,
            },
        )

    return {
        "proposed": True,
        "anchor": anchor_utc.isoformat(),
        "horizonDays": SCHEDULE_HORIZON_DAYS,
        "projectedCompletion": projected.isoformat() if projected else None,
        "scheduleFlag": flag,
        "materialNotBefore": (
            ensure_utc(material_not_before_utc).isoformat()
            if material_not_before_utc is not None
            else None
        ),
        "materialConstraintReason": material_constraint_reason,
        "operations": results,
    }


def validate_schedule(operations, due_date=None):
    """
    Check manual windows for sequence, overlap, and working-hour violations.
    Returns warnings (non-blocking), never raises for conflicts.
    Working-hour checks run against derived segments (not the overnight envelope).
    """
    normalized = sorted(
        [_normalize_operation(op, i) for i, op in enumerate(operations, start=1)],
        key=lambda o: o["sequenceNo"],
    )
    warnings = []

    intervals_by_worker = {}
    intervals_by_machine = {}

    prev_end = None
    for op in normalized:
        if not op.get("scheduledStart") or not op.get("scheduledEnd"):
            continue
        start = ensure_utc(datetime.fromisoformat(op["scheduledStart"].replace("Z", "+00:00")))
        end = ensure_utc(datetime.fromisoformat(op["scheduledEnd"].replace("Z", "+00:00")))
        if end <= start:
            warnings.append(
                {
                    "sequenceNo": op["sequenceNo"],
                    "code": "INVALID_WINDOW",
                    "message": f"Operation {op['sequenceNo']} has end before start",
                }
            )
            continue

        if prev_end and start < prev_end:
            warnings.append(
                {
                    "sequenceNo": op["sequenceNo"],
                    "code": "SEQUENCE_VIOLATION",
                    "message": (
                        f"Operation {op['sequenceNo']} starts before the previous operation completes"
                    ),
                }
            )
        prev_end = end

        wid = op.get("assignedWorkerId")
        segments = []
        if wid:
            schedule_by_dow = load_crew_schedule_map(_crew(op))
            shop_start = utc_to_shop(start)
            shop_end = utc_to_shop(end)
            exceptions = load_calendar_exceptions(shop_start.date(), shop_end.date())
            segments = derive_working_segments(start, end, schedule_by_dow, exceptions)

            if not segments:
                warnings.append(
                    {
                        "sequenceNo": op["sequenceNo"],
                        "code": "OUTSIDE_WORKING_HOURS",
                        "message": (
                            f"Operation {op['sequenceNo']} runs outside working hours on "
                            f"{shop_start.date().isoformat()}"
                        ),
                    }
                )
            else:
                first_start, _ = segments[0]
                _, last_end = segments[-1]
                # Envelope must match accumulated working time (no overhang past shifts).
                if abs((start - first_start).total_seconds()) > 1 or abs(
                    (end - last_end).total_seconds()
                ) > 1:
                    overhang_day = (
                        shop_start.date()
                        if abs((start - first_start).total_seconds()) > 1
                        else shop_end.date()
                    )
                    warnings.append(
                        {
                            "sequenceNo": op["sequenceNo"],
                            "code": "OUTSIDE_WORKING_HOURS",
                            "message": (
                                f"Operation {op['sequenceNo']} runs outside working hours on "
                                f"{overhang_day.isoformat()}"
                            ),
                        }
                    )
                for seg_start, seg_end in segments:
                    seg_shop_start = utc_to_shop(seg_start)
                    seg_shop_end = utc_to_shop(seg_end)
                    cur = seg_shop_start.date()
                    last = seg_shop_end.date()
                    outside = False
                    while cur <= last:
                        day_start_t, day_end_t, is_working = effective_hours_for_date(
                            cur, schedule_by_dow, exceptions
                        )
                        day_lo = datetime.combine(
                            cur, datetime.min.time(), tzinfo=seg_shop_start.tzinfo
                        )
                        day_hi = day_lo + timedelta(days=1)
                        slice_start = max(seg_shop_start, day_lo)
                        slice_end = min(seg_shop_end, day_hi)
                        if slice_start >= slice_end:
                            cur += timedelta(days=1)
                            continue
                        if not is_working or not day_start_t or not day_end_t:
                            outside = True
                            break
                        work_start = datetime.combine(
                            cur, day_start_t, tzinfo=seg_shop_start.tzinfo
                        )
                        work_end = datetime.combine(
                            cur, day_end_t, tzinfo=seg_shop_start.tzinfo
                        )
                        if slice_start < work_start or slice_end > work_end:
                            outside = True
                            break
                        cur += timedelta(days=1)
                    if outside:
                        warnings.append(
                            {
                                "sequenceNo": op["sequenceNo"],
                                "code": "OUTSIDE_WORKING_HOURS",
                                "message": (
                                    f"Operation {op['sequenceNo']} runs outside working hours on "
                                    f"{seg_shop_start.date().isoformat()}"
                                ),
                            }
                        )
                        break

            clash_seq = next(
                (
                    other_seq
                    for member in _crew(op)
                    for seg_start, seg_end in segments
                    for other_start, other_end, other_seq in intervals_by_worker.get(member, [])
                    if seg_start < other_end and other_start < seg_end
                ),
                None,
            )
            if clash_seq is not None:
                warnings.append(
                    {
                        "sequenceNo": op["sequenceNo"],
                        "code": "WORKER_CONFLICT",
                        "message": (
                            f"Worker double-booked on operations {clash_seq} and {op['sequenceNo']}"
                        ),
                    }
                )
            for member in _crew(op):
                intervals_by_worker.setdefault(member, []).extend(
                    (s, e, op["sequenceNo"]) for s, e in segments
                )

        uid = op.get("machineUnitId")
        if uid:
            busy_pieces = segments if segments else [(start, end)]
            for seg_start, seg_end in busy_pieces:
                for other_start, other_end, other_seq in intervals_by_machine.get(uid, []):
                    if seg_start < other_end and other_start < seg_end:
                        warnings.append(
                            {
                                "sequenceNo": op["sequenceNo"],
                                "code": "MACHINE_CONFLICT",
                                "message": (
                                    f"Machine unit double-booked on operations {other_seq} and {op['sequenceNo']}"
                                ),
                            }
                        )
                        break
                else:
                    continue
                break
            intervals_by_machine.setdefault(uid, []).extend(
                (s, e, op["sequenceNo"]) for s, e in busy_pieces
            )

    projected = None
    ends = [
        ensure_utc(datetime.fromisoformat(o["scheduledEnd"].replace("Z", "+00:00")))
        for o in normalized
        if o.get("scheduledEnd")
    ]
    if ends:
        projected = max(ends)

    return {
        "warnings": warnings,
        "projectedCompletion": projected.isoformat() if projected else None,
        "scheduleFlag": compute_schedule_flag(projected, due_date) if due_date else None,
    }


def _overlaps(segments, others) -> bool:
    return any(s < oe and os < e for s, e in segments for os, oe in others)


def schedule_problems(
    operations,
    *,
    exclude_job_id=None,
    material_not_before_utc=None,
    now_utc=None,
    include_warnings=False,
) -> list[dict]:
    """
    Everything that stops a schedule from being confirmed, one entry per problem:
    {sequenceNo, operationId, code, message, severity}. Operations already
    started or completed are kept as they are and only count as bookings.

    include_warnings adds entries with severity "warning" that never block,
    such as a lead placed on another operator's unit (UNIT_OWNER).

    Clashes compare actual working periods, never the whole start-to-end span.
    """
    from app.services.operation_service import open_downtime_intervals_by_unit

    now = ensure_utc(now_utc or datetime.now(timezone.utc))
    ops = []
    for i, raw in enumerate(operations, start=1):
        op = _normalize_operation(raw, i)
        if isinstance(raw, dict) and raw.get("scheduled") is False:
            op["unplacedMessage"] = raw.get("message") or "no time found"
        ops.append(op)
    ops.sort(key=lambda o: o["sequenceNo"])

    external = _external_booking_records(
        exclude_job_id=exclude_job_id,
        exclude_operation_ids=[o["id"] for o in ops if o.get("id")],
    )
    downtime = open_downtime_intervals_by_unit()
    units = {u.id: u for u in MachineUnit.query.all()}
    unit_labels = {uid: u.label for uid, u in units.items()}

    problems = []

    def add(op, code, message, severity="error", **who):
        # ``who``: workerId / machineUnitId of the clashing worker or unit, so
        # the schedule table can show that name in red.
        problems.append(
            {
                "sequenceNo": op["sequenceNo"],
                "operationId": op.get("id"),
                "code": code,
                "message": message,
                "severity": severity,
                **{k: v for k, v in who.items() if v},
            }
        )

    def label(op):
        return f"#{op['sequenceNo']} {op.get('operationName') or 'Operation'}"

    placed = []
    prev = None
    first = True
    for op in ops:
        start = _parse_iso(op.get("scheduledStart"))
        end = _parse_iso(op.get("scheduledEnd"))
        no_hours = (
            no_hours_messages(_crew(op))
            if not op.get("outsourced") and not _frozen_result(op)
            else []
        )
        if no_hours:
            for message in no_hours:
                add(op, "NO_WORKING_HOURS", f"{label(op)}: {message}")
            first = False
            continue
        if op.get("unplacedMessage") or not start or not end:
            reason = op.get("unplacedMessage")
            add(
                op,
                "UNSCHEDULED",
                f"{label(op)} could not be scheduled: {reason}" if reason
                else f"{label(op)} has no scheduled time",
            )
            first = False
            continue

        worker_id = op.get("assignedWorkerId")
        crew = _crew(op)
        schedule_by_dow = load_crew_schedule_map(crew)
        exceptions = load_calendar_exceptions(utc_to_shop(start).date(), utc_to_shop(end).date())

        if _frozen_result(op):
            work_start = _parse_iso(op.get("actualStart")) or start
            segments = derive_working_segments(work_start, end, schedule_by_dow, exceptions)
            placed.append((op, segments or [(work_start, end)]))
            prev = (op, end)
            first = False
            continue

        if first and material_not_before_utc is not None and start < ensure_utc(
            material_not_before_utc
        ):
            add(
                op,
                "MATERIAL_NOT_READY",
                f"{label(op)} starts before {utc_to_shop(material_not_before_utc):%b %d, %Y}, "
                "when the materials can be in",
            )
        first = False

        if start < now:
            add(op, "PAST_START", f"{label(op)} starts in the past ({_fmt_shop(start)})")

        if prev and start < prev[1]:
            add(
                op,
                "SEQUENCE_VIOLATION",
                f"{label(op)} starts before {label(prev[0])} ends ({_fmt_shop(prev[1])})",
            )

        if op.get("outsourced"):
            placed.append((op, [(start, end)]))
            prev = (op, end)
            continue

        segments = derive_working_segments(start, end, schedule_by_dow, exceptions)
        if not segments or segments[0][0] != start or segments[-1][1] != end:
            day = start if not segments or segments[0][0] != start else end
            who = _crew_label(op) if worker_id else "the worker"
            add(
                op,
                "OUTSIDE_WORKING_HOURS",
                f"{label(op)} runs outside working hours or on a holiday "
                f"({who}, {utc_to_shop(day):%a %b %d})",
            )
        busy = segments or [(start, end)]

        # Every crew member is booked for the operation's working periods.
        for member in crew:
            clash = next(
                (o for o, segs in placed if member in _crew(o) and _overlaps(busy, segs)),
                None,
            )
            if clash:
                add(
                    op,
                    "WORKER_CONFLICT",
                    f"{_worker_label(member)} is booked on {label(clash)} at the same time",
                    workerId=member,
                )
                continue
            other = next(
                (
                    (o, segs)
                    for o, segs in external
                    if member in o.crew_ids and _overlaps(busy, segs)
                ),
                None,
            )
            if other:
                o, segs = other
                add(
                    op,
                    "WORKER_CONFLICT",
                    f"{_worker_label(member)} is already booked on "
                    f"{o.job_order.job_number} #{o.sequence_no} {o.operation_name} "
                    f"({_fmt_shop(segs[0][0])})",
                    workerId=member,
                )

        unit_id = op.get("machineUnitId")
        if op.get("machineTypeId") and not unit_id:
            add(
                op,
                "NO_MACHINE_UNIT",
                f"{label(op)} needs a machine unit; choose one or propose the schedule again",
            )
        if unit_id:
            unit = unit_labels.get(unit_id, "The machine unit")
            clash = next(
                (o for o, segs in placed if o.get("machineUnitId") == unit_id and _overlaps(busy, segs)),
                None,
            )
            other = None
            if not clash:
                other = next(
                    (
                        (o, segs)
                        for o, segs in external
                        if o.machine_unit_id == unit_id and _overlaps(busy, segs)
                    ),
                    None,
                )
            if clash:
                add(op, "MACHINE_CONFLICT", f"{unit} is booked on {label(clash)} at the same time")
            elif other:
                o, segs = other
                add(
                    op,
                    "MACHINE_CONFLICT",
                    f"{unit} is already booked on {o.job_order.job_number} "
                    f"#{o.sequence_no} {o.operation_name} ({_fmt_shop(segs[0][0])})",
                )
            elif _overlaps(busy, downtime.get(unit_id, [])):
                add(op, "MACHINE_CONFLICT", f"{unit} has an open breakdown")
            placed_unit = units.get(unit_id)
            if include_warnings and unit_ownership(placed_unit, worker_id) == OTHERS_UNIT:
                add(
                    op,
                    "UNIT_OWNER",
                    f"{label(op)}: {_worker_label(worker_id)} is placed on {placed_unit.label}, "
                    f"{_unit_owner_name(placed_unit)}'s unit",
                    severity="warning",
                )

        placed.append((op, busy))
        prev = (op, end)

    return problems
