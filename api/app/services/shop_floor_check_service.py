"""Checks on work already on the shop floor, and moving work off a broken unit.

* Overruns: an in-progress or paused operation whose projected finish runs
  past its planned end and now clashes with later work of the same worker or
  unit. The affected unstarted operations move later with the normal
  scheduler (workers and units kept, nothing earlier), each move goes into the
  move history, and the Admin is told.
* Stalled: paused for more than one working day without a resume. The Admin
  is told so a person decides (resume, re-plan or reassign); its booking stops
  growing meanwhile (see ``schedule_service.stalled_since``).
* End of shift: work still running after the worker's shift has ended (they
  went home without pausing) is paused at the end of the shift, or at the
  clock-out the Admin recorded, so overnight hours are not counted as work.
* Down units: released unstarted operations booked on a unit that is down are
  flagged; with no expected repair date and no other unit of the type the job
  is marked Needs re-plan. A passed expected repair date on a unit still down
  asks the Admin to update it.

``run_checks`` runs with the other background checks (API start, daily and on
page open) and after a pause or resume is recorded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.job_order import JobOrder, JobOrderStatus
from app.models.machine import MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.models.operation_time import pause_reason_label
from app.models.schedule_move import DelayKind
from app.models.staff_alert import StaffAlertKind
from app.models.user import UserRole
from app.services import material_purchase_service as mp_service
from app.services.schedule_calendar import ensure_utc, shop_now, utc_to_shop
from app.utils.errors import AppError

log = logging.getLogger(__name__)

RELEASED = (JobOrderStatus.SCHEDULED, JobOrderStatus.IN_PROGRESS)
UNSTARTED = (OperationStatus.SCHEDULED, OperationStatus.REWORK)

MOVE_UNIT = "MOVE_UNIT"
AFTER_REPAIR = "AFTER_REPAIR"
LEAVE = "LEAVE"
BREAKDOWN_ACTIONS = (MOVE_UNIT, AFTER_REPAIR, LEAVE)

# A paused operation's projected finish moves with the clock; overlaps shorter
# than this are drift, not a clash worth moving work (and alerting) again.
OVERRUN_SLACK = timedelta(minutes=30)


def _now(now_utc=None) -> datetime:
    return ensure_utc(now_utc or datetime.now(timezone.utc))


def _fmt(dt) -> str:
    return utc_to_shop(ensure_utc(dt)).strftime("%a %d %b %H:%M")


def _label(op) -> str:
    return f"{op.job_order.job_number} #{op.sequence_no} {op.operation_name or 'Operation'}"


def _parse(value) -> datetime:
    return ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _overlaps(a, b) -> bool:
    return any(s < be and bs < e for s, e in a for bs, be in b)


def _released_ops(statuses):
    return (
        JobOperation.query.join(JobOrder, JobOperation.job_order_id == JobOrder.id)
        .filter(JobOrder.status.in_(RELEASED), JobOperation.status.in_(statuses))
        .all()
    )


def _unstarted(op) -> bool:
    return (
        op.status in UNSTARTED
        and op.actual_start is None
        and not op.is_outsourced
        and op.scheduled_start is not None
        and op.scheduled_end is not None
    )


def _admin_alert(kind, title, message, job_id, dedupe_key):
    from app.services.staff_alert_service import raise_alert

    return raise_alert(
        roles=[UserRole.ADMIN],
        kind=kind,
        title=title,
        message=message,
        job_order_id=job_id,
        dedupe_key=dedupe_key,
    )


# --- Moving a job's unstarted work later -------------------------------------


def replan_unstarted(job: JobOrder, now: datetime, *, kind: str, cause: str, title: str,
                     unit_changes: dict | None = None, moved_by_id=None) -> dict:
    """Re-place the job's unstarted operations from now with the normal
    scheduler: saved workers and units kept, nothing earlier, started work kept.
    Records the move and tells the Admin; when it can't be placed nothing moves
    and the job is marked Needs re-plan. Does not commit."""
    from app.services.job_order_service import mark_needs_replan
    from app.services.material_delay_service import record_move
    from app.services.schedule_service import propose_schedule, resolve_material_not_before_utc

    row = {"jobId": job.id, "jobNumber": job.job_number}
    ops = sorted(job.operations, key=lambda o: o.sequence_no or 0)
    floor_date, floor_reason = mp_service.scheduling_material_floor(job)
    floor_utc = (
        None
        if mp_service.job_has_started(job)
        else resolve_material_not_before_utc(job.material_status, None, floor_date)
    )
    proposal = propose_schedule(
        ops,
        job.due_date,
        exclude_job_id=job.id,
        anchor_utc=now,
        material_not_before_utc=floor_utc,
        material_constraint_reason=floor_reason,
        never_earlier=True,
        honor_machine_pins=True,
        suggest_faster_units=False,
    )
    results = {r.get("id"): r for r in proposal["operations"]}
    failed = [r for r in proposal["operations"] if not r.get("scheduled")]
    if failed:
        reason = "; ".join(
            f"#{r['sequenceNo']} {r.get('operationName') or 'Operation'}: {r.get('message') or 'not placed'}"
            for r in failed
        )
        mark_needs_replan(
            job,
            f"{cause}; it could not be moved automatically",
            f"{cause}. It could not be moved automatically: {reason}",
            dedupe_key=f"needs-replan:{kind}:{job.id}",
        )
        row.update(outcome="NO_SLOT", reason=reason)
        return row

    moves = []
    for op in ops:
        if not _unstarted(op) or op.id not in results:
            continue
        r = results[op.id]
        if r.get("assignedWorkerId") and r["assignedWorkerId"] != op.assigned_worker_id:
            raise RuntimeError("an automatic move tried to change a worker")
        start, end = _parse(r["scheduledStart"]), _parse(r["scheduledEnd"])
        old_start = ensure_utc(op.scheduled_start)
        if start < old_start:
            raise RuntimeError("an automatic move tried to place an operation earlier")
        unit_change = (unit_changes or {}).get(op.id)
        if start != old_start or end != ensure_utc(op.scheduled_end) or unit_change:
            moves.append((op, old_start, start, unit_change))
            op.scheduled_start, op.scheduled_end = start, end
    if not moves:
        row.update(outcome="UNCHANGED")
        return row

    def _text(op, old, new, unit_change):
        where = f" from {unit_change[0]} to {unit_change[1]}" if unit_change else ""
        when = f" from {_fmt(old)} to {_fmt(new)}" if new != old else f", still {_fmt(new)}"
        return f"#{op.sequence_no} {op.operation_name or 'Operation'} moved{where}{when}"

    first = min(moves, key=lambda m: m[1])
    reason = f"{cause}. " + "; ".join(_text(*m) for m in moves)
    record_move(job, kind, first[1], first[2], reason, moved_by_id=moved_by_id)
    _admin_alert(StaffAlertKind.WORK_MOVED, title, reason, job.id,
                 f"work-moved:{kind}:{job.id}:{first[2].isoformat()}")
    job.needs_replan_reason = None
    db.session.flush()
    row.update(
        outcome="MOVED",
        previousStart=first[1].isoformat(),
        newStart=first[2].isoformat(),
        reason=reason,
        operations=[
            {"operationId": m[0].id, "sequenceNo": m[0].sequence_no,
             "previousStart": m[1].isoformat(), "newStart": m[2].isoformat()}
            for m in moves
        ],
    )
    return row


# --- Overruns ------------------------------------------------------------------


def overrun_clashes(now_utc=None) -> list[dict]:
    """In-progress (or paused) operations projected to finish after their
    planned end, with the unstarted operations their overrun now clashes with:
    later work of the same worker or unit, and later operations of the same job."""
    from app.services.schedule_service import (
        _operation_booking_envelope,
        _segments_for_worker_envelope,
        operation_working_segments,
    )

    now = _now(now_utc)
    running = _released_ops([OperationStatus.IN_PROGRESS])
    if not running:
        return []
    waiting = [
        (op, operation_working_segments(op, now))
        for op in _released_ops(UNSTARTED)
        if _unstarted(op)
    ]
    found = []
    for r in running:
        envelope = _operation_booking_envelope(r, now)
        if not envelope or not r.scheduled_end:
            continue
        planned_end, finish = ensure_utc(r.scheduled_end), envelope[1]
        cutoff = finish - OVERRUN_SLACK
        if cutoff <= planned_end:
            continue
        over = (
            _segments_for_worker_envelope(r.crew_ids, planned_end, cutoff)
            if r.assigned_worker_id
            else [(planned_end, cutoff)]
        )
        crew = set(r.crew_ids)
        affected = []
        for op, segments in waiting:
            later_in_job = (
                op.job_order_id == r.job_order_id
                and (op.sequence_no or 0) > (r.sequence_no or 0)
                and ensure_utc(op.scheduled_start) < cutoff
            )
            shares = bool(crew & set(op.crew_ids)) or (
                r.machine_unit_id is not None and op.machine_unit_id == r.machine_unit_id
            )
            if later_in_job or (shares and _overlaps(segments, over)):
                affected.append(op)
        if affected:
            found.append({"running": r, "plannedEnd": planned_end, "projectedFinish": finish,
                          "affected": affected})
    return found


def _overrun_cause(clashes) -> str:
    parts = []
    for c in clashes:
        r = c["running"]
        who = r.assigned_worker.full_name if r.assigned_worker else "the crew"
        unit = f", {r.machine_unit.label}" if r.machine_unit else ""
        parts.append(
            f"{_label(r)} ({who}{unit}) is running over: now expected to finish "
            f"{_fmt(c['projectedFinish'])}, after its planned end {_fmt(c['plannedEnd'])}"
        )
    return "; ".join(parts)


def shift_work_behind_overruns(now_utc=None) -> dict:
    """Move unstarted work that clashes with an overrun later, earliest first,
    flushing each job so two never take the same slot. Commits."""
    from app.services.job_order_service import lock_schedule

    now = _now(now_utc)
    try:
        lock_schedule()
        by_job = {}
        for clash in overrun_clashes(now):
            for op in clash["affected"]:
                entry = by_job.setdefault(
                    op.job_order_id,
                    {"job": op.job_order, "first": ensure_utc(op.scheduled_start), "clashes": []},
                )
                entry["first"] = min(entry["first"], ensure_utc(op.scheduled_start))
                if clash not in entry["clashes"]:
                    entry["clashes"].append(clash)
        moved, not_placed = [], []
        for entry in sorted(by_job.values(), key=lambda e: (e["first"], e["job"].job_number or "")):
            job = entry["job"]
            row = replan_unstarted(
                job, now, kind=DelayKind.OVERRUN, cause=_overrun_cause(entry["clashes"]),
                title=f"{job.job_number} moved later: earlier work is running over",
            )
            if row["outcome"] == "MOVED":
                moved.append(row)
            elif row["outcome"] == "NO_SLOT":
                not_placed.append(row)
        db.session.commit()
        return {"moved": moved, "notPlaced": not_placed}
    except Exception:
        db.session.rollback()
        raise


# --- Stalled operations ----------------------------------------------------------


def stalled_operations(now_utc=None) -> list[tuple[JobOperation, datetime]]:
    """(operation, when it stalled) for in-progress work paused over a working day."""
    from app.services.schedule_service import stalled_since

    now = _now(now_utc)
    found = []
    for op in _released_ops([OperationStatus.IN_PROGRESS]):
        since = stalled_since(op, now)
        if since is not None:
            found.append((op, since))
    return found


def flag_stalled(now_utc=None) -> int:
    """One Admin alert per pause that stalled. Does not commit."""
    from app.services.schedule_service import paused_at

    alerts = 0
    for op, _since in stalled_operations(now_utc):
        paused = paused_at(op)
        last = max(op.time_logs, key=lambda L: ensure_utc(L.event_at))
        reason = pause_reason_label(last.reason) if last.reason else "no reason given"
        alerts += len(_admin_alert(
            StaffAlertKind.OPERATION_STALLED,
            f"{_label(op)} is stalled",
            f"Paused since {_fmt(paused)} ({reason}) and not resumed for over a working day. "
            "Its booking is no longer extended. Resume it, re-plan or reassign it.",
            op.job_order_id,
            f"stalled:{op.id}:{paused.isoformat()}",
        ))
    return alerts


# --- Work on units that are down ----------------------------------------------------


def _booked_while_down(op, row, now) -> bool:
    from app.services.operation_service import downtime_blocked_until

    until = downtime_blocked_until(row, now)
    return ensure_utc(op.scheduled_end) > ensure_utc(row.started_at) and (
        until is None or ensure_utc(op.scheduled_start) < until
    )


def work_on_down_units(now_utc=None, unit_id=None) -> list[tuple[JobOperation, object]]:
    """(operation, open downtime) for released unstarted work booked on a unit while it is down."""
    from app.services.operation_service import open_downtimes_by_unit

    now = _now(now_utc)
    downs = open_downtimes_by_unit()
    if unit_id is not None:
        downs = {k: v for k, v in downs.items() if k == unit_id}
    if not downs:
        return []
    return sorted(
        (
            (op, downs[op.machine_unit_id])
            for op in _released_ops(UNSTARTED)
            if _unstarted(op)
            and op.machine_unit_id in downs
            and _booked_while_down(op, downs[op.machine_unit_id], now)
        ),
        key=lambda pair: (ensure_utc(pair[0].scheduled_start), pair[0].sequence_no or 0),
    )


def _other_usable_units(unit) -> list[MachineUnit]:
    from app.services.operation_service import open_downtimes_by_unit

    down = set(open_downtimes_by_unit())
    return [
        u
        for u in MachineUnit.query.filter_by(machine_type_id=unit.machine_type_id, active=True)
        .order_by(MachineUnit.label)
        .all()
        if u.id != unit.id and u.id not in down
    ]


def _no_way_forward(row, now) -> bool:
    from app.services.operation_service import downtime_blocked_until

    return downtime_blocked_until(row, now) is None and not _other_usable_units(row.machine_unit)


def _mark_down_unit_jobs(pairs, now) -> int:
    """No repair date and no other unit of the type: the jobs need a person. Does not commit."""
    from app.services.job_order_service import mark_needs_replan

    marked = 0
    for job_id in dict.fromkeys(op.job_order_id for op, row in pairs if _no_way_forward(row, now)):
        job = db.session.get(JobOrder, job_id)
        unit = next(row.machine_unit for op, row in pairs if op.job_order_id == job_id)
        reason = f"{unit.label} is down with no expected repair date and no other unit to move to"
        if job.needs_replan_reason != reason:
            mark_needs_replan(job, reason, f"{reason}.", dedupe_key=f"needs-replan:down:{job.id}:{unit.id}")
            marked += 1
    return marked


def flag_work_on_down_units(now_utc=None) -> int:
    """One Admin alert per operation and breakdown. Does not commit."""
    now = _now(now_utc)
    pairs = work_on_down_units(now)
    alerts = 0
    for op, row in pairs:
        unit = row.machine_unit
        alerts += len(_admin_alert(
            StaffAlertKind.WORK_ON_DOWN_UNIT,
            f"{_label(op)} is booked on {unit.label}, which is down",
            f"{unit.label} has been down since {_fmt(row.started_at)} ({row.reason}). "
            f"{_label(op)} is booked {_fmt(op.scheduled_start)}. Move it to another "
            f"{unit.machine_type.name if unit.machine_type else 'unit'}, after the repair, or leave it.",
            op.job_order_id,
            f"down-unit-work:{row.id}:{op.id}",
        ))
    _mark_down_unit_jobs(pairs, now)
    return alerts


def flag_passed_repair_dates(now_utc=None) -> int:
    """A unit still down after its expected repair date: ask the Admin to update it. Does not commit."""
    from app.services.operation_service import open_downtimes_by_unit

    today = utc_to_shop(_now(now_utc)).date()
    alerts = 0
    for row in open_downtimes_by_unit().values():
        if row.expected_repair_date and row.expected_repair_date < today:
            unit = row.machine_unit
            alerts += len(_admin_alert(
                StaffAlertKind.REPAIR_DATE_PASSED,
                f"{unit.label}: expected repair date has passed",
                f"{unit.label} was expected back {row.expected_repair_date.strftime('%a %d %b')} "
                "and is still down. Update the expected repair date (or close the breakdown) "
                "so work can be planned around it; until then the unit is treated as down "
                "indefinitely.",
                None,
                f"repair-date-passed:{row.id}:{row.expected_repair_date.isoformat()}",
            ))
    return alerts


# --- Work left running after the shift ended -----------------------------------------

# Time after the end of a shift before running work is paused for the worker,
# so a worker finishing a few minutes late can still pause it themselves.
END_OF_SHIFT_GRACE = timedelta(minutes=30)
AUTO_PAUSE_NOTE = "Paused automatically at the end of the shift"


def _shift_cutoff(worker_id, started_at, attendance_by_key):
    """(pause_at, pause_after) for work running since ``started_at``: the clock
    stops at ``pause_at`` once the time is past ``pause_after``.

    * A clock-out recorded for that shop day, after the start: the clock-out.
    * Else the end of the worker's working hours that day (overtime and
      holidays included; the shop's normal hours if none are set). While the
      worker is clocked in with no clock-out yet, wait until the day is over.
    * Started after the day's hours ended (unplanned overtime): the end of
      that day.
    """
    from app.services.schedule_calendar import (
        default_shop_schedule_by_dow,
        effective_windows_for_date,
        has_working_hours,
        load_calendar_exceptions,
        load_worker_schedule_maps,
        shop_local_to_utc,
    )

    start = ensure_utc(started_at)
    day = utc_to_shop(start).date()
    day_end = shop_local_to_utc(day + timedelta(days=1), datetime.min.time())
    record = attendance_by_key.get((worker_id, day))
    if record is not None and record.clock_out is not None:
        out = ensure_utc(record.clock_out)
        if out > start:
            return out, out + END_OF_SHIFT_GRACE

    schedule = load_worker_schedule_maps(worker_id) if worker_id else {}
    if not has_working_hours(schedule):
        schedule = default_shop_schedule_by_dow()
    windows = effective_windows_for_date(day, schedule, load_calendar_exceptions(day, day))
    ends = [shop_local_to_utc(day, end) for _s, end in windows]
    ends = [e for e in ends if e > start]
    if not ends:
        return day_end, day_end
    shift_end = max(ends)
    still_in = record is not None and record.clock_out is None
    return shift_end, (day_end if still_in else shift_end + END_OF_SHIFT_GRACE)


def auto_pause_after_shift(now_utc=None) -> int:
    """Pause running work whose shift is over. Returns how many. Does not commit."""
    from app.models.attendance import AttendanceRecord
    from app.models.operation_time import OperationPauseReason, OperationTimeEvent
    from app.services.operation_service import _append_log, _last_event

    now = _now(now_utc)
    running = []
    for op in JobOperation.query.filter_by(status=OperationStatus.IN_PROGRESS).all():
        last = _last_event(op)
        if last and last.event in (OperationTimeEvent.START, OperationTimeEvent.RESUME):
            running.append((op, last))
    if not running:
        return 0

    days = {utc_to_shop(ensure_utc(last.event_at)).date() for _op, last in running}
    workers = {last.worker_id or op.assigned_worker_id for op, last in running}
    attendance = {
        (r.worker_id, r.work_date): r
        for r in AttendanceRecord.query.filter(
            AttendanceRecord.worker_id.in_([w for w in workers if w]),
            AttendanceRecord.work_date.in_(days),
        ).all()
    }

    paused = 0
    for op, last in running:
        worker_id = last.worker_id or op.assigned_worker_id
        pause_at, pause_after = _shift_cutoff(worker_id, last.event_at, attendance)
        if now < pause_after:
            continue
        _append_log(
            op,
            worker_id,
            OperationTimeEvent.PAUSE,
            max(pause_at, ensure_utc(last.event_at)),
            reason=OperationPauseReason.END_OF_SHIFT,
            note=AUTO_PAUSE_NOTE,
            received_at=now,
        )
        paused += 1
    if paused:
        db.session.flush()
    return paused


def run_checks(now_utc=None) -> dict:
    """Every check above; overruns first so the flags see the moved work. Commits."""
    now = _now(now_utc)
    try:
        auto_paused = auto_pause_after_shift(now)
        if auto_paused:
            db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Pausing work left running after the shift failed")
        auto_paused = 0
    moved = shift_work_behind_overruns(now)
    try:
        result = {
            "autoPaused": auto_paused,
            "moved": len(moved["moved"]),
            "notPlaced": len(moved["notPlaced"]),
            "stalledAlerts": flag_stalled(now),
            "downUnitAlerts": flag_work_on_down_units(now),
            "repairDateAlerts": flag_passed_repair_dates(now),
            "notClockedInAlerts": _flag_not_clocked_in(now),
        }
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def _flag_not_clocked_in(now) -> int:
    from app.services.worker_cover_service import flag_not_clocked_in

    return flag_not_clocked_in(now)


def safe_run_after_clock_event() -> None:
    """After a pause or resume: the same checks, never failing the request."""
    try:
        shift_work_behind_overruns()
    except Exception:
        db.session.rollback()
        log.exception("Overrun check after a pause or resume failed")


# --- Breakdown report: what to do with the work booked on the unit ----------------


def _serialize(op):
    from app.services.operation_service import _serialize_affected_operation

    return _serialize_affected_operation(op)


def paused_in_progress_on_unit(unit_id) -> list:
    """In-progress work on this unit whose clock is already stopped."""
    from app.models.operation_time import OperationTimeEvent
    from app.services.operation_service import _last_event

    paused = []
    for op in JobOperation.query.filter_by(
        machine_unit_id=unit_id, status=OperationStatus.IN_PROGRESS
    ).all():
        last = _last_event(op)
        if last and last.event == OperationTimeEvent.PAUSE:
            paused.append(op)
    return paused


def _reassign_paused(unit, paused, result):
    """Move paused in-progress work onto another free unit of the same type."""
    others = _other_usable_units(unit)
    if not paused:
        return
    if not others:
        result["left"].extend(op.id for op in paused)
        return
    target = others[0]
    for op in paused:
        op.machine_unit_id = target.id
        result["moved"].append(
            {
                "jobId": op.job_order_id,
                "jobNumber": op.job_order.job_number if op.job_order else None,
                "outcome": "MOVED",
                "reason": (
                    f"Paused for machine breakdown; moved from {unit.label} to {target.label}"
                ),
            }
        )


def breakdown_options(unit_id, now_utc=None) -> dict:
    """Work booked on a down unit, including an operation paused for the breakdown."""
    from app.services.operation_service import downtime_blocked_until, open_downtimes_by_unit

    now = _now(now_utc)
    row = open_downtimes_by_unit().get(unit_id)
    if row is None:
        raise AppError("This unit has no open breakdown.", "NOT_FOUND", 404)
    unit = row.machine_unit
    pairs = work_on_down_units(now, unit_id)
    paused = paused_in_progress_on_unit(unit_id)
    others = _other_usable_units(unit)
    can_after = downtime_blocked_until(row, now) is not None
    has_work = bool(pairs) or bool(paused)
    default = MOVE_UNIT if others else AFTER_REPAIR if can_after else None
    operations = [_serialize(op) for op, _ in pairs]
    seen = {item["id"] for item in operations}
    for op in paused:
        if op.id in seen:
            continue
        item = _serialize(op)
        item["paused"] = True
        item["pauseReason"] = "Machine breakdown"
        operations.append(item)
    return {
        "downtimeId": row.id,
        "machineUnitId": unit.id,
        "machineUnitLabel": unit.label,
        "expectedRepairDate": row.expected_repair_date.isoformat() if row.expected_repair_date else None,
        "operations": operations,
        "otherUnits": [
            {"id": u.id, "label": u.label,
             "operatorName": u.default_operator.full_name if u.default_operator else None}
            for u in others
        ],
        "canMoveToUnit": bool(others),
        "canMoveAfterRepair": can_after,
        "needsReplan": has_work and not others and not can_after,
        "defaultAction": default,
    }


def _owner_rank(unit, lead_id, start, end, worker_busy) -> int:
    """0 no operator or the lead's own unit, 1 its operator is free then, 2 its operator is busy then."""
    owner = unit.default_operator_id
    if not owner or owner == lead_id:
        return 0
    busy = worker_busy.get(owner, [])
    return 2 if any(s < end and start < e for s, e in busy) else 1


def _best_unit_for(op, job, candidates, now, bookings):
    """The candidate unit where the operation starts earliest, then one with no
    operator or a free operator, then label; None when none can take it."""
    from app.services.schedule_service import propose_schedule

    best = None
    original = op.machine_unit_id
    for unit in candidates:
        op.machine_unit_id = unit.id
        proposal = propose_schedule(
            sorted(job.operations, key=lambda o: o.sequence_no or 0),
            job.due_date,
            exclude_job_id=job.id,
            anchor_utc=now,
            never_earlier=True,
            honor_machine_pins=True,
            suggest_faster_units=False,
            _bookings=bookings,
        )
        placed = next((r for r in proposal["operations"] if r.get("id") == op.id), None)
        if placed and placed.get("scheduled"):
            start, end = _parse(placed["scheduledStart"]), _parse(placed["scheduledEnd"])
            key = (start, _owner_rank(unit, op.assigned_worker_id, start, end, bookings[0]), unit.label or "")
            if best is None or key < best[0]:
                best = (key, unit)
    op.machine_unit_id = original
    return best[1] if best else None


def resolve_breakdown(unit_id, action, now_utc=None, actor_id=None) -> dict:
    """Apply the Admin's choice for the work booked on a down unit. MOVE_UNIT:
    each operation to the free unit of the same type where it starts earliest
    (no operator or a free operator preferred). AFTER_REPAIR: the same unit,
    after the expected repair date. LEAVE: nothing moves (the background check
    keeps flagging it). With no repair date and no other unit the jobs are
    marked Needs re-plan. Commits."""
    from app.services.job_order_service import lock_schedule
    from app.services.operation_service import downtime_blocked_until
    from app.services.schedule_service import _load_external_bookings

    if action not in BREAKDOWN_ACTIONS:
        raise AppError(f"action must be one of {', '.join(BREAKDOWN_ACTIONS)}", "VALIDATION_ERROR", 400)
    now = _now(now_utc)
    try:
        lock_schedule()
        options = breakdown_options(unit_id, now)
        pairs = work_on_down_units(now, unit_id)
        paused = paused_in_progress_on_unit(unit_id)
        result = {"action": action, "moved": [], "notPlaced": [], "needsReplan": [], "left": []}
        if action == MOVE_UNIT:
            unit_row = db.session.get(MachineUnit, unit_id)
            if unit_row is not None:
                _reassign_paused(unit_row, paused, result)
        if not pairs or action == LEAVE:
            result["left"] = [op.id for op, _ in pairs]
            db.session.commit()
            return result
        row = pairs[0][1]
        unit = row.machine_unit
        if options["needsReplan"]:
            _mark_down_unit_jobs(pairs, now)
            result["needsReplan"] = list(dict.fromkeys(op.job_order.job_number for op, _ in pairs))
            db.session.commit()
            return result
        if action == AFTER_REPAIR and downtime_blocked_until(row, now) is None:
            raise AppError(
                f"{unit.label} has no expected repair date in the future; set one first.",
                "CONFLICT",
                409,
            )
        by_job = {}
        for op, _ in pairs:
            by_job.setdefault(op.job_order_id, []).append(op)
        others = _other_usable_units(unit)
        for job_id, ops in by_job.items():
            job = db.session.get(JobOrder, job_id)
            unit_changes = {}
            if action == MOVE_UNIT:
                bookings = _load_external_bookings(
                    exclude_job_id=job.id, exclude_operation_ids=[o.id for o in job.operations]
                )
                for op in sorted(ops, key=lambda o: o.sequence_no or 0):
                    target = _best_unit_for(op, job, others, now, bookings)
                    if target is not None:
                        unit_changes[op.id] = (unit.label, target.label)
                        op.machine_unit_id = target.id
            cause = (
                f"{unit.label} is down ({row.reason})"
                + ("" if action == MOVE_UNIT else f"; expected repair {row.expected_repair_date:%a %d %b}")
            )
            outcome = replan_unstarted(
                job, now, kind=DelayKind.BREAKDOWN, cause=cause,
                title=f"{job.job_number} moved: {unit.label} is down",
                unit_changes=unit_changes,
                moved_by_id=actor_id,
            )
            {"MOVED": result["moved"], "NO_SLOT": result["notPlaced"]}.get(
                outcome["outcome"], result["left"]
            ).append(outcome)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise
