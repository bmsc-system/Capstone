"""What happens to a worker's operations when the plan stops fitting the day.

- Absent, on leave, or an emergency in the middle of the day: the worker is
  marked away for a period (``WorkerAbsence``) and their work in that period is
  covered automatically. Work that has not started goes to the best available
  replacement at its booked time; when nobody can take it, the job's unstarted
  steps move to after the worker is back. Work already started is handed to a
  helper on the crew or to a replacement, who resumes it. The Administrator is
  told what was done and can change any of it (``hand_over``).
- Finished early: the job's next steps are pulled earlier when the people and
  machines they need are free (``pull_forward``).
- Finished late: handled by ``shop_floor_check_service`` (later work is moved).

The scheduler reads the away periods (``absence_intervals``), so nothing new is
booked on a worker while they are away.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from app.extensions import db
from app.models.job_order import JobOrder, JobOrderStatus
from app.models.operation import JobOperation, OperationStatus
from app.models.operation_time import OperationPauseReason, OperationTimeEvent
from app.models.schedule_move import DelayKind
from app.models.staff_alert import StaffAlertKind
from app.models.user import User, UserRole, UserStatus
from app.models.worker_absence import AbsenceReason, WorkerAbsence
from app.services.schedule_calendar import ensure_utc, shop_local_to_utc, shop_now, utc_to_shop
from app.utils.errors import AppError

log = logging.getLogger(__name__)

RELEASED = (JobOrderStatus.SCHEDULED, JobOrderStatus.IN_PROGRESS)
UNSTARTED = (OperationStatus.PENDING, OperationStatus.SCHEDULED)
OPEN = (*UNSTARTED, OperationStatus.IN_PROGRESS, OperationStatus.REWORK)
# An early finish pulls later steps forward only when it frees at least this much.
PULL_FORWARD_MIN = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt) -> str:
    return utc_to_shop(ensure_utc(dt)).strftime("%b %d, %I:%M %p").replace(" 0", " ")


def _label(op) -> str:
    job = op.job_order
    name = f"#{op.sequence_no} {op.operation_name or 'Operation'}"
    return f"{name} ({job.job_number})" if job and job.job_number else name


# --- Away periods ---------------------------------------------------------------


def active_absences(worker_ids=None, start=None, end=None) -> list[WorkerAbsence]:
    """Away periods not cancelled, overlapping [start, end] (default: from now on)."""
    start = ensure_utc(start) if start else _now()
    q = WorkerAbsence.query.filter(
        WorkerAbsence.cancelled_at.is_(None), WorkerAbsence.ends_at > start
    )
    if end is not None:
        q = q.filter(WorkerAbsence.starts_at < ensure_utc(end))
    if worker_ids is not None:
        ids = list(worker_ids)
        if not ids:
            return []
        q = q.filter(WorkerAbsence.worker_id.in_(ids))
    return q.all()


def absence_intervals(worker_ids=None) -> dict:
    """{worker id: [(start, end)]} of current and coming away periods, for the scheduler."""
    out = {}
    for row in active_absences(worker_ids):
        out.setdefault(row.worker_id, []).append((ensure_utc(row.starts_at), ensure_utc(row.ends_at)))
    return out


def away_worker_ids(start, end) -> set:
    """Workers away at any point in [start, end]."""
    if not start or not end:
        now = _now()
        return {r.worker_id for r in active_absences(start=now, end=now + timedelta(seconds=1))}
    return {r.worker_id for r in active_absences(start=start, end=end)}


def _worker(worker_id) -> User:
    worker = db.session.get(User, worker_id) if worker_id else None
    if worker is None or worker.role != UserRole.PRODUCTION_WORKER:
        raise AppError("Pick a production worker.", "VALIDATION_ERROR", 400)
    return worker


def _parse_day(value, label) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise AppError(f"Enter the {label}.", "VALIDATION_ERROR", 400)


# --- Finding who can take the work ----------------------------------------------


def replacements(op: JobOperation, start, end, *, exclude=(), as_helper_for=None) -> list[dict]:
    """Production workers who can take ``op`` in [start, end], best first:
    qualified for its machine, working then, not booked and not away. People
    not clocked in today come last."""
    from app.services.worker_suggestion_service import suggest_workers

    result = suggest_workers(
        scheduled_start=ensure_utc(start).isoformat(),
        scheduled_end=ensure_utc(end).isoformat(),
        exclude_operation_id=op.id,
        machine_type_id=op.machine_type_id,
        operation_type_id=op.operation_type_id,
        operation_name=op.operation_name,
        lead_id=as_helper_for,
        exclude_worker_ids=list(exclude),
    )
    skip = set(exclude) | set(op.crew_ids)
    seen, rows = set(), []
    for s in result["suggestions"]:
        wid = s["workerId"]
        if wid in skip or wid in seen or s.get("role") != UserRole.PRODUCTION_WORKER.value:
            continue
        if s.get("qualified") is False or s.get("available") is False:
            continue
        seen.add(wid)
        rows.append(
            {
                "workerId": wid,
                "fullName": s["fullName"],
                "score": s.get("score"),
                "proficiency": s.get("proficiency"),
                "attendanceWarning": s.get("attendanceWarning"),
                "reasons": s.get("reasons"),
            }
        )
    rows.sort(key=lambda r: r["attendanceWarning"] is not None)
    return rows


def _remaining_window(op: JobOperation, now: datetime):
    """When a started operation would run if resumed now: the hours left of its target."""
    from app.services.schedule_calendar import shop_working_intervals

    worked = float(op.actual_worked_hours or 0)
    left = max(float(op.estimated_hours or 1) - worked, 0.5)
    # Outside working hours (evening, Sunday): from the next time the shop is open.
    open_times = shop_working_intervals(now, now + timedelta(days=10))
    start = max(now, open_times[0][0]) if open_times else now
    return start, start + timedelta(hours=left)


def _window(op: JobOperation, now: datetime):
    if op.status in UNSTARTED and op.scheduled_start and op.scheduled_end:
        start, end = ensure_utc(op.scheduled_start), ensure_utc(op.scheduled_end)
        if end > now:
            return max(start, now), end
    return _remaining_window(op, now)


def _audit(op, before_lead, before_helpers, why):
    from app.services.audit_service import write_audit_event

    write_audit_event(
        "OPERATION_CREW_CHANGED",
        "JobOperation",
        op.id,
        before={"assignedWorkerId": before_lead, "helperIds": before_helpers},
        after={
            "assignedWorkerId": op.assigned_worker_id,
            "helperIds": list(op.helper_ids),
            "reason": why,
        },
    )


def _set_lead(op: JobOperation, new_lead_id: str, why: str, *, drop=()):
    """Make ``new_lead_id`` the lead; ``drop`` leaves the crew. Does not commit."""
    before_lead, before_helpers = op.assigned_worker_id, list(op.helper_ids)
    helpers = [h for h in before_helpers if h != new_lead_id and h not in drop]
    if before_lead and before_lead != new_lead_id and before_lead not in drop:
        helpers.append(before_lead)
    op.set_helpers(helpers)
    op.assigned_worker_id = new_lead_id
    _audit(op, before_lead, before_helpers, why)


# --- Manual change by the Administrator ------------------------------------------


def hand_over(op: JobOperation, worker_id=None, *, actor_id=None) -> dict:
    """Give a released, unfinished operation to another lead: at its booked time
    when not started, or to resume it when it was started and is paused. With no
    worker named, the best available one is picked. Commits."""
    job = op.job_order
    if job is None or job.status not in RELEASED:
        raise AppError("Only released jobs can be handed over.", "INVALID_TRANSITION", 409)
    if op.status not in OPEN:
        raise AppError("This operation is already completed.", "INVALID_TRANSITION", 409)
    if op.is_outsourced:
        raise AppError("Outsourced work has no worker.", "VALIDATION_ERROR", 400)
    from app.services.operation_service import _is_running

    if _is_running(op):
        raise AppError(
            "This operation is running now. It has to be paused before someone else takes it.",
            "INVALID_TRANSITION",
            409,
        )
    now = _now()
    start, end = _window(op, now)
    previous = op.assigned_worker
    if worker_id:
        new = db.session.get(User, worker_id)
        if new is None or new.status == UserStatus.DISABLED:
            raise AppError("Pick an active worker.", "VALIDATION_ERROR", 400)
        if new.id == op.assigned_worker_id:
            raise AppError(f"{new.full_name} already leads this operation.", "VALIDATION_ERROR", 400)
        if new.id not in op.helper_ids:
            ok = {r["workerId"] for r in replacements(op, start, end)}
            if new.id not in ok:
                raise AppError(
                    f"{new.full_name} is not free, not working or not skilled for this operation "
                    f"from {_fmt(start)} to {_fmt(end)}.",
                    "WORKER_NOT_AVAILABLE",
                    409,
                )
    else:
        options = replacements(op, start, end)
        if not options:
            raise AppError(
                "Nobody qualified is free for this operation at that time.",
                "NO_REPLACEMENT",
                409,
            )
        new = db.session.get(User, options[0]["workerId"])
    drop = (previous.id,) if previous is not None else ()
    _set_lead(op, new.id, "Handed over by the Administrator", drop=drop)
    db.session.commit()
    return {
        "operationId": op.id,
        "label": _label(op),
        "from": previous.full_name if previous else None,
        "to": new.full_name,
        "toId": new.id,
    }


# --- Covering an away worker ------------------------------------------------------


def _pause_for(op: JobOperation, worker_id: str, now: datetime, reason, note: str):
    from app.services.operation_service import _append_log, _is_running

    if _is_running(op):
        _append_log(op, worker_id, OperationTimeEvent.PAUSE, now, reason=reason, note=note)


def cover(worker: User, start: datetime, end: datetime, *, cause: str, emergency=False) -> dict:
    """Cover ``worker``'s unfinished operations that fall in [start, end]. Returns
    what was done per operation. Does not commit; the away period must already
    be saved so the scheduler sees it."""
    from app.services.shop_floor_check_service import replan_unstarted

    now = _now()
    until = utc_to_shop(end).strftime("%b %d").replace(" 0", " ")
    ops = (
        JobOperation.query.join(JobOrder, JobOperation.job_order_id == JobOrder.id)
        .filter(
            JobOperation.crew_includes(worker.id),
            JobOperation.status.in_(OPEN),
            JobOrder.status.in_(RELEASED),
        )
        .all()
    )
    rows, to_replan = [], {}
    far = datetime.max.replace(tzinfo=timezone.utc)
    for op in sorted(ops, key=lambda o: o.scheduled_start or far):
        if op.is_outsourced:
            continue
        started = op.status not in UNSTARTED
        if started and start > now:
            # Leave entered ahead of time: work already running stays with the
            # worker, who may still finish it before the leave begins.
            continue
        if not started:
            if not op.scheduled_start or not op.scheduled_end:
                continue
            if not (ensure_utc(op.scheduled_start) < end and ensure_utc(op.scheduled_end) > start):
                continue
        row = {"operationId": op.id, "jobId": op.job_order_id, "label": _label(op)}
        win_start, win_end = _window(op, now)

        if op.assigned_worker_id != worker.id:
            # A helper: bring in another helper when one is free, else go on without.
            options = replacements(
                op, win_start, win_end, exclude=(worker.id,), as_helper_for=op.assigned_worker_id
            )
            before_lead, before_helpers = op.assigned_worker_id, list(op.helper_ids)
            helpers = [h for h in before_helpers if h != worker.id]
            if options:
                helpers.append(options[0]["workerId"])
                row.update(outcome="HELPER_REPLACED", to=options[0]["fullName"])
            else:
                row.update(outcome="HELPER_REMOVED")
            op.set_helpers(helpers)
            _audit(op, before_lead, before_helpers, cause)
            rows.append(row)
            continue

        if started:
            _pause_for(
                op,
                worker.id,
                now,
                OperationPauseReason.EMERGENCY if emergency else OperationPauseReason.WORKER_AWAY,
                cause,
            )
            helpers = [h for h in op.helper_ids if h not in away_worker_ids(win_start, win_end)]
            if helpers:
                new = db.session.get(User, helpers[0])
                _set_lead(op, new.id, cause, drop=(worker.id,))
                row.update(outcome="HANDED_OVER", to=new.full_name)
            else:
                options = replacements(op, win_start, win_end, exclude=(worker.id,))
                if options:
                    _set_lead(op, options[0]["workerId"], cause, drop=(worker.id,))
                    row.update(outcome="HANDED_OVER", to=options[0]["fullName"])
                else:
                    row.update(outcome="LEFT_PAUSED")
            rows.append(row)
            continue

        options = replacements(op, win_start, win_end, exclude=(worker.id,))
        if options:
            _set_lead(op, options[0]["workerId"], cause, drop=(worker.id,))
            row.update(outcome="REASSIGNED", to=options[0]["fullName"])
            rows.append(row)
        else:
            to_replan.setdefault(op.job_order_id, []).append(row)

    db.session.flush()
    for job_id, pending in to_replan.items():
        job = db.session.get(JobOrder, job_id)
        result = replan_unstarted(
            job,
            now,
            kind=DelayKind.WORKER_ABSENT,
            cause=f"{cause}; nobody else was free to take the work",
            title=f"{job.job_number} moved: {worker.full_name} is away until {until}",
        )
        for row in pending:
            if result.get("outcome") == "MOVED":
                moved = {m["operationId"]: m for m in result.get("operations", [])}
                new_start = moved.get(row["operationId"], {}).get("newStart")
                row.update(outcome="MOVED_LATER", newStart=new_start)
            elif result.get("outcome") == "NO_SLOT":
                row.update(outcome="NEEDS_REPLAN")
            else:
                row.update(outcome="UNCHANGED")
            rows.append(row)
    return {"workerId": worker.id, "workerName": worker.full_name, "operations": rows}


OUTCOME_TEXT = {
    "REASSIGNED": "given to {to}",
    "HANDED_OVER": "handed over to {to}, who can resume it",
    "HELPER_REPLACED": "{to} helps instead",
    "HELPER_REMOVED": "goes on without this helper",
    "MOVED_LATER": "moved later, nobody else was free",
    "NEEDS_REPLAN": "could not be moved; needs a new plan",
    "LEFT_PAUSED": "left paused; nobody is free to take it",
    "UNCHANGED": "kept as it is",
}


def summary_text(result: dict) -> str:
    rows = result.get("operations") or []
    if not rows:
        return "No scheduled work was affected."
    return "; ".join(
        f"{r['label']}: {OUTCOME_TEXT.get(r.get('outcome'), 'kept as it is').format(to=r.get('to') or 'someone else')}"
        for r in rows
    )


def _alert(worker: User, absence: WorkerAbsence, result: dict):
    from app.services.staff_alert_service import raise_alert

    until = utc_to_shop(absence.ends_at).strftime("%b %d").replace(" 0", " ")
    word = AbsenceReason.LABELS.get(absence.reason, "Away").lower()
    raise_alert(
        roles=[UserRole.ADMIN],
        kind=StaffAlertKind.WORK_MOVED,
        title=f"{worker.full_name}: {word} until {until}. Their work was covered",
        message=summary_text(result),
        dedupe_key=f"worker-away:{absence.id}",
    )


def mark_away(worker_id, from_date=None, to_date=None, reason=None, note=None, *, actor_id=None,
              starts_now=False) -> dict:
    """Mark a worker away and cover their work for the period. ``starts_now``:
    the period starts at this moment (an emergency), else at the start of
    ``from_date``. Commits."""
    worker = _worker(worker_id)
    today = shop_now().date()
    first = _parse_day(from_date, "first day away") if from_date else today
    last = _parse_day(to_date, "last day away") if to_date else first
    if last < first:
        raise AppError("The last day away is before the first.", "VALIDATION_ERROR", 400)
    if last < today:
        raise AppError("Those days have already passed.", "VALIDATION_ERROR", 400)
    reason = (reason or AbsenceReason.ABSENT).upper()
    if reason not in AbsenceReason.ALL:
        raise AppError("Pick Absent, Emergency or On leave.", "VALIDATION_ERROR", 400)
    now = _now()
    start = now if starts_now or first <= today else shop_local_to_utc(first, time(0, 0))
    end = shop_local_to_utc(last, time(23, 59, 59))
    if end <= start:
        raise AppError("Those days have already passed.", "VALIDATION_ERROR", 400)
    # One open period per worker: a new one replaces what it overlaps.
    for old in active_absences([worker.id], start=start, end=end):
        old.cancelled_at = now
    absence = WorkerAbsence(
        worker_id=worker.id,
        starts_at=start,
        ends_at=end,
        reason=reason,
        note=(str(note or "").strip() or None),
        created_by_id=actor_id,
    )
    db.session.add(absence)
    db.session.flush()
    until = utc_to_shop(end).strftime("%b %d").replace(" 0", " ")
    cause = f"{worker.full_name} is away until {until} ({AbsenceReason.LABELS[reason].lower()})"
    result = cover(worker, start, end, cause=cause, emergency=reason == AbsenceReason.EMERGENCY)
    _alert(worker, absence, result)
    db.session.commit()
    return {"absence": absence.to_dict(), "cover": result, "summary": summary_text(result)}


def cover_after_emergency(worker_id: str) -> dict | None:
    """The worker paused with Emergency: away for the rest of today, work
    covered. Never raises (the pause itself is already saved)."""
    try:
        return mark_away(
            worker_id,
            reason=AbsenceReason.EMERGENCY,
            note="Paused work with Emergency",
            actor_id=worker_id,
            starts_now=True,
        )
    except Exception:  # noqa: BLE001
        db.session.rollback()
        log.exception("Covering work after an emergency pause failed worker=%s", worker_id)
        return None


def cancel_absence(absence_id: str) -> WorkerAbsence:
    """The worker is back: the period stops blocking the schedule. Work already
    handed to others stays with them. Commits."""
    absence = db.session.get(WorkerAbsence, absence_id)
    if absence is None:
        raise AppError("Away period not found", "NOT_FOUND", 404)
    if absence.cancelled_at is None:
        absence.cancelled_at = _now()
        db.session.commit()
    return absence


def list_absences(on_date=None) -> list[dict]:
    """Away periods covering ``on_date`` (default today), plus coming ones."""
    day = _parse_day(on_date, "date") if on_date else shop_now().date()
    start = shop_local_to_utc(day, time(0, 0))
    rows = WorkerAbsence.query.filter(
        WorkerAbsence.cancelled_at.is_(None), WorkerAbsence.ends_at > start
    ).order_by(WorkerAbsence.starts_at.asc())
    return [r.to_dict() for r in rows.all()]


# --- Not clocked in ---------------------------------------------------------------

NOT_IN_GRACE = timedelta(minutes=60)


def flag_not_clocked_in(now_utc=None) -> int:
    """Tell the Administrator about a worker with operations today who has not
    clocked in an hour after their start time and is not marked away. One alert
    per worker and day. Does not commit."""
    from app.services.attendance_service import not_clocked_in_today
    from app.services.schedule_calendar import (
        effective_hours_for_date,
        load_calendar_exceptions,
        load_worker_schedule_maps_many,
    )
    from app.services.staff_alert_service import raise_alert

    now = ensure_utc(now_utc) if now_utc else _now()
    today = utc_to_shop(now).date()
    day_start = shop_local_to_utc(today, time(0, 0))
    day_end = shop_local_to_utc(today, time(23, 59, 59))
    ops = (
        JobOperation.query.join(JobOrder, JobOperation.job_order_id == JobOrder.id)
        .filter(
            JobOperation.status.in_(UNSTARTED),
            JobOrder.status.in_(RELEASED),
            JobOperation.scheduled_start >= day_start,
            JobOperation.scheduled_start <= day_end,
        )
        .all()
    )
    by_worker = {}
    for op in ops:
        if op.assigned_worker_id and not op.is_outsourced:
            by_worker.setdefault(op.assigned_worker_id, []).append(op)
    if not by_worker:
        return 0
    missing = not_clocked_in_today(list(by_worker))
    away = away_worker_ids(day_start, day_end)
    schedules = load_worker_schedule_maps_many(list(missing))
    exceptions = load_calendar_exceptions(today, today)
    raised = 0
    for wid in missing:
        if wid in away:
            continue
        start_t, _end_t, working = effective_hours_for_date(today, schedules.get(wid) or {}, exceptions)
        if not working or not start_t or now < shop_local_to_utc(today, start_t) + NOT_IN_GRACE:
            continue
        worker = db.session.get(User, wid)
        if worker is None or worker.role != UserRole.PRODUCTION_WORKER:
            continue
        count = len(by_worker[wid])
        raised += len(
            raise_alert(
                roles=[UserRole.ADMIN],
                kind=StaffAlertKind.OPERATION_STALLED,
                title=f"{worker.full_name} has not clocked in",
                message=(
                    f"{count} operation{'s' if count != 1 else ''} booked today: "
                    + ", ".join(_label(o) for o in by_worker[wid][:4])
                    + ". Clock them in on the Attendance page, or mark them absent there "
                    "and their work is handed to others."
                ),
                dedupe_key=f"not-in:{wid}:{today.isoformat()}",
            )
        )
    return raised


# --- Finished early ----------------------------------------------------------------


def pull_forward(job: JobOrder, now_utc=None) -> dict | None:
    """After a step finished ahead of its booked end: place the job's unstarted
    steps again from now, and apply it only when every step starts no later
    than before and at least one starts ``PULL_FORWARD_MIN`` sooner. Only free
    time is used; other jobs are not moved. Does not commit."""
    from app.services import material_purchase_service as mp_service
    from app.services.material_delay_service import record_move
    from app.services.schedule_service import propose_schedule
    from app.services.staff_alert_service import raise_alert

    if job is None or job.status not in RELEASED:
        return None
    now = ensure_utc(now_utc) if now_utc else _now()
    ops = sorted(job.operations, key=lambda o: o.sequence_no or 0)
    waiting = [
        o for o in ops
        if o.status in UNSTARTED and o.actual_start is None and not o.is_outsourced
        and o.scheduled_start is not None and o.scheduled_end is not None
    ]
    if not waiting or any(o.is_outsourced and o.status != OperationStatus.COMPLETED for o in ops):
        return None
    if not mp_service.job_has_started(job):
        return None
    proposal = propose_schedule(
        ops,
        job.due_date,
        exclude_job_id=job.id,
        anchor_utc=now,
        honor_machine_pins=True,
        suggest_faster_units=False,
    )
    results = {r.get("id"): r for r in proposal["operations"]}
    moves = []
    for op in waiting:
        r = results.get(op.id)
        if not r or not r.get("scheduled"):
            return None
        if r.get("assignedWorkerId") and r["assignedWorkerId"] != op.assigned_worker_id:
            return None
        start = datetime.fromisoformat(str(r["scheduledStart"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(r["scheduledEnd"]).replace("Z", "+00:00"))
        old_start, old_end = ensure_utc(op.scheduled_start), ensure_utc(op.scheduled_end)
        if start > old_start or end > old_end:
            return None
        moves.append((op, old_start, ensure_utc(start), ensure_utc(end)))
    if not any(old - new >= PULL_FORWARD_MIN for _op, old, new, _end in moves):
        return None
    changed = [m for m in moves if m[1] != m[2]]
    for op, _old, start, end in changed:
        op.scheduled_start, op.scheduled_end = start, end
    first = min(changed, key=lambda m: m[1])
    reason = "An earlier step finished ahead of schedule. " + "; ".join(
        f"#{op.sequence_no} {op.operation_name or 'Operation'} moved earlier from {_fmt(old)} to {_fmt(new)}"
        for op, old, new, _end in changed
    )
    # An earlier start is not a delay: the move goes in the history, and the
    # job's own delay fields stay as they were.
    kept = (
        job.material_delay_original_start,
        job.material_delay_reason,
        job.material_delay_supplier_order_id,
        job.material_delayed_at,
        job.delay_kind,
    )
    record_move(job, DelayKind.EARLY_FINISH, first[1], first[2], reason)
    (
        job.material_delay_original_start,
        job.material_delay_reason,
        job.material_delay_supplier_order_id,
        job.material_delayed_at,
        job.delay_kind,
    ) = kept
    raise_alert(
        roles=[UserRole.ADMIN],
        kind=StaffAlertKind.WORK_MOVED,
        title=f"{job.job_number} moved earlier: a step finished ahead of schedule",
        message=reason,
        job_order_id=job.id,
        dedupe_key=f"work-earlier:{job.id}:{first[2].isoformat()}",
    )
    db.session.flush()
    return {
        "jobId": job.id,
        "operations": [
            {"operationId": op.id, "previousStart": old.isoformat(), "newStart": new.isoformat()}
            for op, old, new, _end in changed
        ],
    }


def safe_pull_forward(job_id: str) -> None:
    """After a completion; never fails the request. Commits."""
    try:
        job = db.session.get(JobOrder, job_id)
        if pull_forward(job):
            db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        log.exception("Pulling work forward after an early finish failed job=%s", job_id)
