"""Rank people for an operation's crew with fixed weights.

Availability is a filter, not a weight: people booked in the window, off
shift, or on a holiday are left out. While planning a job (no times yet),
the filter is the job's date required: a worker (and unit) that cannot do
this step in time for the job to finish by its date required is marked
``meetsDate: false`` and ranked after every one that can; when nobody can,
the closest finish comes first. Equal scores go to the one free sooner.
Three kinds of suggestion:

  Machine operation, lead: each (worker, machine unit) pair scores
      0.40 x assigned operator of that unit (1 or 0)
    + 0.30 x machine skill level / 5
    + 0.20 x past performance
    + 0.10 x current workload
  and each worker is shown with their best unit (or, in pair mode, once per
  unit they could use, with when that worker and unit are next free). A unit
  with an assigned operator is offered to that operator; units with none (or
  whose operator can't take the work) are open to anyone qualified.

  Operation without a machine, lead: 0.60 x past performance + 0.40 x workload.

  Helper (after the lead is chosen, lead excluded, no skill needed):
  0.50 x past performance + 0.50 x workload.
"""

from app.models.machine import MachineType, MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.models.user import UserRole
from app.models.worker_skill import OperationType
from app.services.schedule_calendar import (
    derive_working_segments,
    load_calendar_exceptions,
    load_worker_schedule_maps_many,
    utc_to_shop,
)
from app.services.scoring_service import (
    HELPER_WEIGHTS,
    MACHINE_LEAD_WEIGHTS,
    NO_MACHINE_WEIGHTS,
    combine_score,
    current_week_bounds,
    fetch_efficiency_pairs,
    log_weights_used,
    score_efficiency,
    score_workload,
    worker_week_hours,
    worker_week_load_hours,
)
from app.services.worker_availability import _parse_dt, _windows_overlap, get_busy_workers
from app.services.worker_profile_service import (
    NO_MACHINE_SKILL_RECORDED,
    machine_skill_holders,
    query_assignable_workers,
)

MODE_MACHINE_LEAD = "MACHINE_LEAD"
MODE_NO_MACHINE = "NO_MACHINE"
MODE_HELPER = "HELPER"

NOT_CLOCKED_IN = "Not clocked in today"
NO_SKILL_DATA_SCORE = 0.5


def _working_during(workers, scheduled_start, scheduled_end):
    """Keep workers with working hours inside the window (schedule + work calendar)."""
    start = _parse_dt(scheduled_start)
    end = _parse_dt(scheduled_end)
    if not start or not end or end <= start:
        return workers
    schedules = load_worker_schedule_maps_many([w.id for w in workers])
    exceptions = load_calendar_exceptions(utc_to_shop(start).date(), utc_to_shop(end).date())
    return [
        w
        for w in workers
        if derive_working_segments(start, end, schedules.get(w.id, {}), exceptions)
    ]


def _not_clocked_in_for_today_op(worker_ids, scheduled_start, operation_id):
    """Workers past their start time without a clock-in, when the operation
    starts today. Its start is the proposed window, else the saved schedule."""
    from app.services.attendance_service import not_clocked_in_today
    from app.services.schedule_calendar import shop_now

    start = _parse_dt(scheduled_start)
    if start is None and operation_id:
        op = JobOperation.query.get(operation_id)
        start = op.scheduled_start if op else None
    if start is None or utc_to_shop(start).date() != shop_now().date():
        return set()
    return not_clocked_in_today(worker_ids)


def _find_operation_type(operation_name):
    return OperationType.query.filter(
        (OperationType.name.ilike(operation_name))
        | (OperationType.code.ilike(str(operation_name).replace(" ", "_")))
    ).first()


def _resolve_machine_type_id(*, machine_type_id=None, operation_type_id=None, operation_name=None):
    if machine_type_id:
        return machine_type_id
    ot = OperationType.query.get(operation_type_id) if operation_type_id else None
    if ot is None and operation_name:
        ot = _find_operation_type(operation_name)
    return ot.default_machine_type_id if ot and ot.default_machine_type_id else None


def _resolve_operation_type_id(*, operation_type_id=None, operation_name=None):
    if operation_type_id:
        return operation_type_id
    if operation_name:
        ot = _find_operation_type(operation_name)
        if ot:
            return ot.id
    return None


def _usable_units(machine_type_id, scheduled_start, scheduled_end, exclude_operation_id):
    """Active units of the type that are not broken down and, when a window is
    proposed, not booked by another operation in it."""
    from app.services.operation_service import open_downtimes_by_unit

    units = (
        MachineUnit.query.filter_by(machine_type_id=machine_type_id, active=True)
        .order_by(MachineUnit.label)
        .all()
    )
    down = open_downtimes_by_unit()
    units = [u for u in units if u.id not in down]
    start, end = _parse_dt(scheduled_start), _parse_dt(scheduled_end)
    if not units or not start or not end or end <= start:
        return units
    from app.models.job_order import JobOrder, JobOrderStatus

    booked = (
        JobOperation.query.join(JobOrder, JobOperation.job_order_id == JobOrder.id)
        .filter(
            JobOperation.machine_unit_id.in_([u.id for u in units]),
            JobOperation.status.in_(
                (OperationStatus.SCHEDULED, OperationStatus.IN_PROGRESS, OperationStatus.REWORK)
            ),
            JobOrder.status.notin_((JobOrderStatus.DRAFT, JobOrderStatus.CANCELLED)),
            JobOperation.scheduled_start.isnot(None),
            JobOperation.scheduled_end.isnot(None),
        )
        .all()
    )
    taken = {
        op.machine_unit_id
        for op in booked
        if op.id != exclude_operation_id
        and _windows_overlap(start, end, op.scheduled_start, op.scheduled_end)
    }
    return [u for u in units if u.id not in taken]


def _available_people(
    *, scheduled_start, scheduled_end, exclude_operation_id, exclude_ids=()
):
    workers = query_assignable_workers().all()
    excluded = set(exclude_ids or ())
    busy = get_busy_workers(
        start=scheduled_start, end=scheduled_end, exclude_operation_id=exclude_operation_id
    )
    workers = [w for w in workers if w.id not in busy and w.id not in excluded]
    return _working_during(workers, scheduled_start, scheduled_end)


def _performance_and_load(workers, operation_type_id, exclude_operation_id):
    """{worker id: (efficiency score, reason, workload score, reason)}."""
    load = {
        w.id: worker_week_load_hours(w.id, exclude_operation_id=exclude_operation_id)
        for w in workers
    }
    week_start, week_end = current_week_bounds()
    schedules = load_worker_schedule_maps_many([w.id for w in workers])
    exceptions = load_calendar_exceptions(
        utc_to_shop(week_start).date(), utc_to_shop(week_end).date()
    )
    out = {}
    for w in workers:
        eff, eff_reason, _ = score_efficiency(fetch_efficiency_pairs(w.id, operation_type_id))
        week = worker_week_hours(schedules.get(w.id) or {}, exceptions)
        work, work_reason, _ = score_workload(load[w.id], week)
        out[w.id] = (eff, eff_reason, work, work_reason)
    return out


def _person(worker):
    return {
        "workerId": worker.id,
        "fullName": worker.full_name,
        "nickname": worker.nickname,
        "photoVersion": worker.photo_version,
        "role": worker.role.value,
        "email": worker.email,
        "skills": [s.machine_type.code for s in (worker.skills or []) if s.machine_type],
        "qualified": True,
        "available": True,
        "machineUnitId": None,
        "machineUnitLabel": None,
        "isUnitOperator": False,
        "proficiency": None,
        "matchedSkills": [],
    }


def _unit_text(machine, unit, is_operator):
    if unit is None:
        return f"No free {machine.name} unit"
    if is_operator:
        return f"Assigned operator of {unit.label}"
    if not unit.default_operator_id:
        return f"{unit.label} has no assigned operator"
    return f"{unit.label}, whose operator is not free"


def _machine_lead_suggestions(workers, machine, units, perf, *, pairs=False):
    """One row per worker on their best unit, or with ``pairs`` one row per
    (worker, unit) they could use."""
    holders = machine_skill_holders(machine.id)
    no_skill_yet = holders is None
    if not no_skill_yet:
        workers = [w for w in workers if w.id in holders]
    available_ids = {w.id for w in workers}
    out = []
    for w in workers:
        skill = None if no_skill_yet else holders.get(w.id)
        skill_score = (
            NO_SKILL_DATA_SCORE if skill is None else min(1.0, float(skill.proficiency) / 5.0)
        )
        skill_text = (
            NO_MACHINE_SKILL_RECORDED if skill is None else f"skill {int(skill.proficiency)}/5"
        )
        # Their own unit, or any unit whose operator is not free for this work.
        own_or_open = [
            u
            for u in units
            if u.default_operator_id == w.id
            or not u.default_operator_id
            or u.default_operator_id not in available_ids
        ]
        eff, eff_reason, work, work_reason = perf[w.id]
        scored = []
        for unit in own_or_open or [None]:
            is_operator = bool(unit and unit.default_operator_id == w.id)
            components = {
                "operator": 1.0 if is_operator else 0.0,
                "skill": round(skill_score, 4),
                "efficiency": round(eff, 4),
                "workload": round(work, 4),
            }
            score = combine_score(MACHINE_LEAD_WEIGHTS, components)
            scored.append((score, unit, is_operator, components))
        if not pairs:
            best = None
            for item in scored:
                if best is None or item[0] > best[0]:
                    best = item
            scored = [best]
        for score, unit, is_operator, components in scored:
            row = _person(w)
            row.update(
                score=score,
                components=components,
                reason=", ".join(
                    [_unit_text(machine, unit, is_operator), skill_text, work_reason, eff_reason]
                ),
                machineUnitId=unit.id if unit else None,
                machineUnitLabel=unit.label if unit else None,
                isUnitOperator=is_operator,
                proficiency=skill.proficiency if skill else None,
                matchedSkills=[machine.code] if skill else [],
            )
            out.append(row)
    return out


def _attach_next_free(suggestions, hours, exclude_job_id, needs_unit):
    """nextFreeStart / nextFreeEnd on each row: when that worker (and unit) can
    next take ``hours`` of work. A machine lead without a unit gets none."""
    from app.services.schedule_service import next_free_for_pairs

    keys = [(s["workerId"], s.get("machineUnitId")) for s in suggestions]
    usable = [k for k in keys if k[1] or not needs_unit]
    free = next_free_for_pairs(usable, hours, exclude_job_id=exclude_job_id) if usable else {}
    for s, key in zip(suggestions, keys):
        start, end = free.get(key, (None, None))
        s["nextFreeStart"] = start.isoformat() if start else None
        s["nextFreeEnd"] = end.isoformat() if end else None


def _move_working_hours(start, hours, *, backward=False):
    """``start`` moved by ``hours`` of shop working time (forward, or back)."""
    from datetime import timedelta

    from app.services.schedule_calendar import ensure_utc, shop_working_intervals

    left = timedelta(hours=max(float(hours or 0), 0))
    if left <= timedelta(0):
        return start
    start = ensure_utc(start)
    span = timedelta(days=120)
    if backward:
        intervals = shop_working_intervals(start - span, start)
        for a, b in reversed(intervals):
            if b - a >= left:
                return b - left
            left -= b - a
        return start - span
    for a, b in shop_working_intervals(start, start + span):
        if b - a >= left:
            return a + left
        left -= b - a
    return start + span


def job_step_window(job_id, hours_before=0, hours_after=0):
    """(earliest start, latest finish) for one step of a job being planned:
    after the material is in and the earlier steps' target hours, and early
    enough to leave the later steps' target hours before the date required.
    (None, None) when the job is unknown."""
    from datetime import timezone

    from app.models.job_order import JobOrder
    from app.services import material_purchase_service as mp_service
    from app.services.schedule_calendar import ensure_utc, shop_local_to_utc, shop_now
    from app.services.schedule_service import resolve_material_not_before_utc
    from datetime import time, timedelta

    job = JobOrder.query.get(job_id) if job_id else None
    if job is None or job.due_date is None:
        return None, None
    now = shop_now().astimezone(timezone.utc)
    earliest = now
    try:
        ready_date, _reason = mp_service.scheduling_material_floor(job)
        floor = resolve_material_not_before_utc(job.material_status, None, ready_date)
        if floor is not None:
            earliest = max(earliest, ensure_utc(floor))
    except Exception:  # material date unknown: planning shows that separately
        pass
    earliest = _move_working_hours(earliest, hours_before)
    due_end = shop_local_to_utc(job.due_date + timedelta(days=1), time(0, 0))
    latest = _move_working_hours(due_end, hours_after, backward=True)
    return earliest, latest


def _apply_date_gate(suggestions, hours, exclude_job_id, needs_unit, hours_before, hours_after):
    """Mark each row meetsDate (can do this step in time) and its earliest
    start/finish from the step's earliest start. Rows that cannot are not
    removed: they go after every row that can (closest finish first)."""
    from app.services.schedule_service import next_free_for_pairs

    earliest, latest = job_step_window(exclude_job_id, hours_before, hours_after)
    if earliest is None:
        return False
    keys = [(s["workerId"], s.get("machineUnitId")) for s in suggestions]
    usable = [k for k in keys if k[1] or not needs_unit]
    free = (
        next_free_for_pairs(usable, hours, exclude_job_id=exclude_job_id, anchor_utc=earliest)
        if usable
        else {}
    )
    for s, key in zip(suggestions, keys):
        start, end = free.get(key, (None, None))
        s["nextFreeStart"] = start.isoformat() if start else None
        s["nextFreeEnd"] = end.isoformat() if end else None
        s["meetsDate"] = bool(end and end <= latest)
        s["latestFinish"] = latest.isoformat()
    return True


def _gate_sort_key(s):
    """Rows that meet the date first (best score, then free sooner); then the
    rest by how soon they could finish."""
    far = "9999"
    if s.get("meetsDate") is False:
        return (1, s.get("nextFreeEnd") or far, -s["score"], s["fullName"], s.get("machineUnitLabel") or "")
    return (
        0,
        -round(s["score"], 6),
        s.get("nextFreeStart") or far,
        -(s.get("proficiency") or 0),
        s["fullName"],
        s.get("machineUnitLabel") or "",
    )


def _weighted_suggestions(workers, weights, perf):
    out = []
    for w in workers:
        eff, eff_reason, work, work_reason = perf[w.id]
        components = {"efficiency": round(eff, 4), "workload": round(work, 4)}
        row = _person(w)
        row.update(
            score=combine_score(weights, components),
            components=components,
            reason=", ".join([eff_reason, work_reason]),
        )
        out.append(row)
    return out


def suggest_workers(
    operations=None,
    exclude_job_id=None,
    scheduled_start=None,
    scheduled_end=None,
    exclude_operation_id=None,
    machine_type_id=None,
    operation_type_id=None,
    operation_name=None,
    lead_id=None,
    exclude_worker_ids=None,
    pairs=False,
    estimated_hours=None,
    hours_before=None,
    hours_after=None,
):
    """Suggestions for the lead, or for helpers when ``lead_id`` is given.

    ``pairs``: machine leads come as every (worker, unit) pair they could use,
    and every row carries nextFreeStart/nextFreeEnd for ``estimated_hours``
    (the default target when unset), outside ``exclude_job_id``.

    Returns {"mode", "weights", "suggestions"}; only available people appear.
    """

    if operations and not operation_name:
        if isinstance(operations, str):
            operation_name = operations
        elif isinstance(operations, list) and operations:
            first = operations[0]
            if isinstance(first, dict):
                operation_name = first.get("operationName") or first.get("name")
                machine_type_id = machine_type_id or first.get("machineTypeId")
                operation_type_id = operation_type_id or first.get("operationTypeId")
            else:
                operation_name = first

    target_machine_id = _resolve_machine_type_id(
        machine_type_id=machine_type_id,
        operation_type_id=operation_type_id,
        operation_name=operation_name,
    )
    op_type_id = _resolve_operation_type_id(
        operation_type_id=operation_type_id, operation_name=operation_name
    )
    if lead_id:
        mode, weights = MODE_HELPER, HELPER_WEIGHTS
    elif target_machine_id:
        mode, weights = MODE_MACHINE_LEAD, MACHINE_LEAD_WEIGHTS
    else:
        mode, weights = MODE_NO_MACHINE, NO_MACHINE_WEIGHTS
    log_weights_used(weights, context=f"suggest {mode}")

    workers = _available_people(
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        exclude_operation_id=exclude_operation_id,
        exclude_ids=[lead_id, *(exclude_worker_ids or [])] if lead_id else (),
    )
    perf = _performance_and_load(workers, op_type_id, exclude_operation_id)

    machine = MachineType.query.get(target_machine_id) if mode == MODE_MACHINE_LEAD else None
    if machine is not None:
        units = _usable_units(machine.id, scheduled_start, scheduled_end, exclude_operation_id)
        suggestions = _machine_lead_suggestions(workers, machine, units, perf, pairs=pairs)
    else:
        suggestions = _weighted_suggestions(workers, weights, perf)

    # Attendance is kept for production workers only.
    missing = _not_clocked_in_for_today_op(
        [s["workerId"] for s in suggestions if s["role"] == UserRole.PRODUCTION_WORKER.value],
        scheduled_start,
        exclude_operation_id,
    )
    for s in suggestions:
        s["attendanceWarning"] = NOT_CLOCKED_IN if s["workerId"] in missing else None

    gated = False
    if mode != MODE_HELPER and exclude_job_id and not scheduled_start:
        # Planning a job: the date required is the availability gate.
        gated = _apply_date_gate(
            suggestions,
            estimated_hours,
            exclude_job_id,
            machine is not None,
            hours_before,
            hours_after,
        )
    if gated:
        suggestions.sort(key=_gate_sort_key)
    else:
        suggestions.sort(
            key=lambda s: (
                -s["score"],
                -(s.get("proficiency") or 0),
                s["fullName"],
                s.get("machineUnitLabel") or "",
            )
        )
        if pairs:
            _attach_next_free(suggestions, estimated_hours, exclude_job_id, machine is not None)
    meeting = [s for s in suggestions if s.get("meetsDate") is not False]
    return {
        "mode": mode,
        "weights": dict(weights),
        "suggestions": suggestions,
        "dateGate": {
            "applied": gated,
            "anyMeetsDate": bool(meeting) if gated else None,
            "latestFinish": suggestions[0].get("latestFinish") if gated and suggestions else None,
        },
    }
