"""Operation start/pause/resume/complete with append-only time logs."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.constants.machines import machine_unit_prefix
from app.extensions import db
from app.models.job_order import JobOrder, JobOrderStatus
from app.models.operation import JobOperation, OperationStatus
from app.models.operation_time import (
    MachineDowntime,
    OperationPauseReason,
    OperationTimeEvent,
    OperationTimeLog,
)
from app.models.user import UserRole
from app.services import material_purchase_service as mp_service
from app.services.job_order_service import (
    advance_part_condition,
    check_job_access,
    derive_job_status,
)
from app.utils.errors import AppError



def _notify_after_start(job, operation, before_status):
    """Client email: Started on the job's first operation, Final stage when the
    last one starts. Sent after the commit; never raises."""
    from app.models.notification import NotificationMilestone
    from app.services.job_update_service import is_final_stage_start
    from app.services.notification_service import safe_notify_job_milestone

    try:
        if before_status != JobOrderStatus.IN_PROGRESS and job.status == JobOrderStatus.IN_PROGRESS:
            safe_notify_job_milestone(job.id, NotificationMilestone.JOB_STARTED)
        elif is_final_stage_start(job, operation):
            safe_notify_job_milestone(job.id, NotificationMilestone.JOB_FINAL_STAGE)
    except Exception:  # noqa: BLE001 - an email must never fail the action
        pass


def _notify_after_complete(job):
    """Client email: Halfway once about half the target hours are done. The
    finished job is announced by Ready for delivery, not here."""
    from app.models.notification import NotificationMilestone
    from app.services.job_update_service import halfway_reached
    from app.services.notification_service import safe_notify_job_milestone

    try:
        if job.status != JobOrderStatus.COMPLETED and halfway_reached(job):
            safe_notify_job_milestone(job.id, NotificationMilestone.JOB_HALFWAY)
    except Exception:  # noqa: BLE001
        pass

def _alert_operation_done(operation, job):
    """Tell the Admin an operation is finished, or that the whole job is when it
    was the last one. Added to the caller's transaction."""
    from app.models.staff_alert import StaffAlertKind
    from app.services.staff_alert_service import raise_alert

    name = operation.operation_name or (
        operation.operation_type.name if operation.operation_type else "Operation"
    )
    unit = operation.machine_unit.label if operation.machine_unit else None
    worker = operation.assigned_worker
    who = (worker.nickname or worker.full_name) if worker else None
    number = job.job_number or job.title
    done_at = _ensure_utc(operation.actual_end).isoformat() if operation.actual_end else ""

    if job.status == JobOrderStatus.COMPLETED:
        last = " on ".join(p for p in (name, unit) if p)
        raise_alert(
            roles=[UserRole.ADMIN],
            kind=StaffAlertKind.JOB_DONE,
            title=f"{number} finished: all operations done",
            message=f"Last: {last}{f' by {who}' if who else ''}. Ready for the sales invoice.",
            job_order_id=job.id,
            dedupe_key=f"job-done:{job.id}:{done_at}",
        )
        return

    parts = [p for p in (unit, who) if p]
    if operation.actual_worked_hours is not None and operation.estimated_hours:
        parts.append(
            f"{float(operation.actual_worked_hours):.1f} h of {float(operation.estimated_hours):.1f} h target"
        )
    elif operation.is_outsourced and operation.returned_date:
        parts.append("returned")
    nxt = next(
        (
            o
            for o in sorted(job.operations or [], key=lambda o: o.sequence_no or 0)
            if o.status != OperationStatus.COMPLETED
        ),
        None,
    )
    if nxt is not None:
        nxt_unit = nxt.machine_unit.label if nxt.machine_unit else None
        parts.append(f"Next: {nxt.operation_name}{f' on {nxt_unit}' if nxt_unit else ''}")
    raise_alert(
        roles=[UserRole.ADMIN],
        kind=StaffAlertKind.OPERATION_DONE,
        title=f"{name} finished · {number}",
        message=" · ".join(parts) or None,
        job_order_id=job.id,
        dedupe_key=f"op-done:{operation.id}:{done_at}",
    )


def _parse_timestamp(value):
    if value is None or value == "":
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _ensure_utc(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def list_my_operations(worker_id):
    from sqlalchemy.orm import joinedload, selectinload
    from app.models.job_order import JobOrder, PRODUCTION_VISIBLE_STATUSES
    from app.models.material_purchase import MaterialPurchase

    return (
        JobOperation.query.options(
            joinedload(JobOperation.job_order).joinedload(JobOrder.client),
            joinedload(JobOperation.job_order).selectinload(JobOrder.operations),
            joinedload(JobOperation.job_order)
            .selectinload(JobOrder.material_purchases)
            .joinedload(MaterialPurchase.supplier_order),
            joinedload(JobOperation.machine_type),
            joinedload(JobOperation.assigned_worker),
            joinedload(JobOperation.time_logs),
        )
        .join(JobOrder, JobOperation.job_order_id == JobOrder.id)
        .filter(
            JobOperation.crew_includes(worker_id),
            JobOrder.status.in_(tuple(PRODUCTION_VISIBLE_STATUSES)),
        )
        .order_by(JobOperation.sequence_no.asc())
        .all()
    )


def _assert_worker_owns(operation, user_id, user_role):
    # Crew-gated for every role (including Admin): only the lead or a helper
    # may start/pause/resume/complete. Checked first so a worker who was taken
    # off the crew is told so, not just "Access denied".
    if user_id not in operation.crew_ids:
        name = operation.operation_name or "This operation"
        assignee = operation.assigned_worker
        raise AppError(
            f"{name} is now assigned to {assignee.full_name}. Ask the office if you did this work."
            if assignee
            else f"{name} is no longer assigned to you. Ask the office if you did this work.",
            "OPERATION_REASSIGNED",
            403,
        )
    check_job_access(operation.job_order, user_id, user_role)


CLOCK_WRONG_MESSAGE = "The phone's clock appears to be wrong. Check that automatic time is on."
_CLOCK_FUTURE_TOLERANCE = timedelta(minutes=2)


def check_action_time(event_at, received_at, operation=None, job=None):
    """Refuse a device timestamp that cannot be right: more than two minutes
    ahead of the server, before the operation's previous action, or before
    the job was released."""
    at = _ensure_utc(event_at)
    clock_wrong = at > _ensure_utc(received_at) + _CLOCK_FUTURE_TOLERANCE
    if not clock_wrong and operation is not None:
        last = _last_event(operation)
        clock_wrong = last is not None and at < _ensure_utc(last.event_at)
    job = job if job is not None else (operation.job_order if operation is not None else None)
    if not clock_wrong and job is not None and job.released_at is not None:
        clock_wrong = at < _ensure_utc(job.released_at)
    if clock_wrong:
        raise AppError(CLOCK_WRONG_MESSAGE, "CLOCK_WRONG", 422)


def _append_log(
    operation, worker_id, event, event_at, reason=None, note=None, received_at=None
):
    log = OperationTimeLog(
        operation_id=operation.id,
        worker_id=worker_id,
        event=event,
        event_at=_ensure_utc(event_at),
        reason=reason,
        note=note,
        received_at=received_at,
    )
    db.session.add(log)
    return log


def _ordered_logs(operation):
    logs = list(operation.time_logs or [])
    logs.sort(key=lambda L: (_ensure_utc(L.event_at), L.created_at or _ensure_utc(L.event_at)))
    return logs


def compute_worked_hours(operation) -> Decimal | None:
    """Sum START/RESUME → PAUSE/COMPLETE intervals, minus the daily break.
    Ignores gaps while paused."""
    from app.services.schedule_calendar import hours_excluding_break

    logs = _ordered_logs(operation)
    if not logs:
        return None

    total_seconds = 0.0
    open_start = None
    saw_close = False
    for log in logs:
        ev = log.event
        at = _ensure_utc(log.event_at)
        if ev in (OperationTimeEvent.START, OperationTimeEvent.RESUME):
            if open_start is None:
                open_start = at
        elif ev in (OperationTimeEvent.PAUSE, OperationTimeEvent.COMPLETE):
            if open_start is not None and at > open_start:
                total_seconds += hours_excluding_break(open_start, at) * 3600.0
                saw_close = True
            open_start = None

    if not saw_close:
        return None
    return Decimal(str(round(total_seconds / 3600.0, 4)))


def recompute_variance(operation):
    worked = compute_worked_hours(operation)
    operation.actual_worked_hours = worked
    est = operation.estimated_hours
    if worked is None or est is None or Decimal(str(est)) == 0:
        operation.variance_hours = None
        operation.variance_pct = None
        return
    est_d = Decimal(str(est))
    variance = worked - est_d
    operation.variance_hours = variance
    operation.variance_pct = (variance / est_d) * Decimal("100")


def _last_event(operation):
    logs = _ordered_logs(operation)
    return logs[-1] if logs else None


def _assert_materials_arrived(job, user_role):
    """Start gate for a job's first operation (see ``material_start_block``)."""
    block = mp_service.material_start_block(
        job, for_worker=user_role == UserRole.PRODUCTION_WORKER.value
    )
    if block:
        raise AppError(block["message"], block["code"], 409)


def _assert_earlier_operations_completed(operation):
    waiting_on = [
        op
        for op in sorted(operation.job_order.operations, key=lambda o: o.sequence_no or 0)
        if (op.sequence_no or 0) < (operation.sequence_no or 0)
        and op.status != OperationStatus.COMPLETED
    ]
    if waiting_on:
        names = ", ".join(
            f"#{op.sequence_no} {op.operation_name or 'Operation'}" for op in waiting_on
        )
        raise AppError(
            f"Finish the earlier operations first: {names}.",
            "PRIOR_OPERATION_INCOMPLETE",
            409,
        )


def start_operation(operation, user_id, user_role, timestamp, received_at=None):
    from app.constants.machines import assert_machine_type_available
    from app.models.job_order import JobOrderStatus

    _assert_not_outsourced(operation)
    _assert_worker_owns(operation, user_id, user_role)

    job = operation.job_order
    if job.status == JobOrderStatus.DRAFT:
        raise AppError(
            "This job has not been released to production yet",
            "INVALID_TRANSITION",
            409,
        )
    if job.status == JobOrderStatus.CANCELLED:
        raise AppError("This job was cancelled.", "JOB_CANCELLED", 409)

    if operation.status == OperationStatus.IN_PROGRESS:
        last = _last_event(operation)
        if last and last.event == OperationTimeEvent.START:
            return operation
        if last and last.event in (OperationTimeEvent.START, OperationTimeEvent.RESUME):
            return operation

    if operation.status == OperationStatus.COMPLETED:
        raise AppError(
            "Cannot start a completed operation", "INVALID_TRANSITION", 409
        )

    _assert_earlier_operations_completed(operation)

    assert_machine_type_available(
        operation.machine_type_id,
        exclude_operation_id=operation.id,
    )
    if operation.machine_unit_id:
        _assert_unit_not_down(operation.machine_unit_id)

    first_start = not mp_service.job_has_started(job)
    if first_start:
        _assert_materials_arrived(job, user_role)

    ts = _parse_timestamp(timestamp)
    if received_at is not None:
        check_action_time(ts, received_at, operation)
    before_status = job.status
    try:
        if first_start:
            mp_service.consume_received_lines(job, ts)
        operation.status = OperationStatus.IN_PROGRESS
        if not operation.actual_start:
            operation.actual_start = ts
        _append_log(
            operation,
            user_id,
            OperationTimeEvent.START,
            ts,
            received_at=received_at,
        )
        job.status = derive_job_status(job)
        db.session.commit()
        _notify_after_start(job, operation, before_status)
        return operation
    except Exception:
        db.session.rollback()
        raise


def pause_operation(
    operation, user_id, user_role, reason, note=None, timestamp=None, received_at=None
):
    _assert_worker_owns(operation, user_id, user_role)

    if operation.status != OperationStatus.IN_PROGRESS:
        raise AppError(
            "Only in-progress operations can be paused", "INVALID_TRANSITION", 409
        )

    last = _last_event(operation)
    if last and last.event == OperationTimeEvent.PAUSE:
        return operation
    if not last or last.event not in (
        OperationTimeEvent.START,
        OperationTimeEvent.RESUME,
    ):
        raise AppError(
            "Operation is not actively running", "INVALID_TRANSITION", 409
        )

    try:
        pause_reason = OperationPauseReason(reason) if reason else None
    except ValueError:
        raise AppError("Invalid pause reason", "VALIDATION_ERROR", 400)

    if pause_reason is None:
        raise AppError("reason is required to pause", "VALIDATION_ERROR", 400)

    ts = _parse_timestamp(timestamp)
    if received_at is not None:
        check_action_time(ts, received_at, operation)
    try:
        _append_log(
            operation,
            user_id,
            OperationTimeEvent.PAUSE,
            ts,
            reason=pause_reason,
            note=note,
            received_at=received_at,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    _after_clock_event()
    return operation


def _after_clock_event():
    from app.services.shop_floor_check_service import safe_run_after_clock_event

    safe_run_after_clock_event()


def resume_operation(operation, user_id, user_role, timestamp=None, received_at=None):
    from app.constants.machines import assert_machine_type_available

    _assert_worker_owns(operation, user_id, user_role)

    if operation.status != OperationStatus.IN_PROGRESS:
        raise AppError(
            "Only in-progress operations can be resumed", "INVALID_TRANSITION", 409
        )

    last = _last_event(operation)
    if not last or last.event != OperationTimeEvent.PAUSE:
        raise AppError(
            "Operation is not paused", "INVALID_TRANSITION", 409
        )

    assert_machine_type_available(
        operation.machine_type_id,
        exclude_operation_id=operation.id,
    )
    if operation.machine_unit_id:
        _assert_unit_not_down(operation.machine_unit_id)

    ts = _parse_timestamp(timestamp)
    if received_at is not None:
        check_action_time(ts, received_at, operation)
    try:
        _append_log(
            operation,
            user_id,
            OperationTimeEvent.RESUME,
            ts,
            received_at=received_at,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    _after_clock_event()
    return operation


def complete_operation(operation, user_id, user_role, timestamp, received_at=None):
    _assert_not_outsourced(operation)
    _assert_worker_owns(operation, user_id, user_role)

    if operation.status == OperationStatus.COMPLETED:
        return operation

    if operation.status in (OperationStatus.PENDING, OperationStatus.SCHEDULED):
        raise AppError(
            "Operation must be started before completing", "INVALID_TRANSITION", 409
        )

    last = _last_event(operation)
    if last and last.event == OperationTimeEvent.PAUSE:
        raise AppError(
            "Resume the operation before completing", "INVALID_TRANSITION", 409
        )

    ts = _parse_timestamp(timestamp)
    if received_at is not None:
        check_action_time(ts, received_at, operation)
    job = operation.job_order
    before_status = job.status
    try:
        if not operation.actual_start:
            operation.actual_start = ts
        operation.status = OperationStatus.COMPLETED
        operation.actual_end = ts
        _append_log(
            operation,
            user_id,
            OperationTimeEvent.COMPLETE,
            ts,
            received_at=received_at,
        )
        # Refresh relationship for variance calc
        db.session.flush()
        recompute_variance(operation)
        from sqlalchemy.orm import joinedload
        from app.models.job_order import JobOrder

        job = (
            JobOrder.query.options(
                joinedload(JobOrder.operations).joinedload(JobOperation.operation_type),
            ).get(job.id)
        )
        job.status = derive_job_status(job)
        advance_part_condition(job)
        _alert_operation_done(operation, job)
        db.session.commit()
        _notify_after_complete(job)
        return operation
    except Exception:
        db.session.rollback()
        raise


def _assert_not_outsourced(operation):
    if operation.is_outsourced:
        raise AppError(
            f"{operation.operation_name or 'This operation'} is done outside the shop. "
            "The office records when it is sent out and returned.",
            "OPERATION_OUTSOURCED",
            409,
        )


def _parse_shop_date(value, field):
    from datetime import date as date_cls

    if not value:
        raise AppError(f"{field} is required", "VALIDATION_ERROR", 400)
    if isinstance(value, date_cls):
        return value
    try:
        return date_cls.fromisoformat(str(value)[:10])
    except ValueError:
        raise AppError(f"{field} must be a date (YYYY-MM-DD)", "VALIDATION_ERROR", 400)


def _shop_moment(day, hour):
    """Now when ``day`` is today in the shop, otherwise ``hour``:00 shop time that day."""
    from datetime import time as time_cls

    from app.services.schedule_calendar import SHOP_TZ, shop_now

    now = shop_now()
    if day == now.date():
        return now.astimezone(timezone.utc)
    return datetime.combine(day, time_cls(hour, 0), tzinfo=SHOP_TZ).astimezone(timezone.utc)


def _reload_job_with_types(job_id):
    from sqlalchemy.orm import joinedload

    return JobOrder.query.options(
        joinedload(JobOrder.operations).joinedload(JobOperation.operation_type),
    ).get(job_id)


def send_out_operation(operation, user_role, sent_out_date, sent_to):
    """The Admin records an outsourced operation leaving the shop."""
    from datetime import timedelta

    from app.services.schedule_calendar import shop_now

    if not operation.is_outsourced:
        raise AppError(
            "Only outsourced operations are sent out", "NOT_OUTSOURCED", 409
        )
    job = operation.job_order
    if job.status == JobOrderStatus.DRAFT:
        raise AppError(
            "This job has not been released to production yet",
            "INVALID_TRANSITION",
            409,
        )
    if operation.status == OperationStatus.COMPLETED:
        raise AppError("This operation has already returned", "INVALID_TRANSITION", 409)
    if operation.sent_out_date is not None:
        raise AppError("This operation has already been sent out", "INVALID_TRANSITION", 409)

    day = _parse_shop_date(sent_out_date, "sentOutDate")
    if day > shop_now().date():
        raise AppError("The sent-out date cannot be in the future", "VALIDATION_ERROR", 400)
    where = (sent_to or "").strip()
    if not where:
        raise AppError("Say where it was sent", "VALIDATION_ERROR", 400)
    turnaround = operation.turnaround_days or (
        operation.operation_type.default_turnaround_days if operation.operation_type else None
    )
    if not turnaround:
        raise AppError(
            "Set the turnaround in days before sending it out", "VALIDATION_ERROR", 400
        )

    _assert_earlier_operations_completed(operation)
    first_start = not mp_service.job_has_started(job)
    if first_start:
        _assert_materials_arrived(job, user_role)

    ts = _shop_moment(day, 8)
    before_status = job.status
    try:
        if first_start:
            mp_service.consume_received_lines(job, ts)
        operation.status = OperationStatus.IN_PROGRESS
        operation.actual_start = ts
        operation.sent_out_date = day
        operation.sent_to = where[:255]
        operation.turnaround_days = int(turnaround)
        operation.scheduled_start = ts
        operation.scheduled_end = ts + timedelta(days=int(turnaround))
        operation.assigned_worker_id = None
        operation.set_helpers([])
        operation.machine_unit_id = None
        job.status = derive_job_status(job)
        db.session.commit()
        _notify_after_start(job, operation, before_status)
        return operation
    except Exception:
        db.session.rollback()
        raise


def return_operation(operation, returned_date):
    """Admin or Office records an outsourced operation coming back."""
    from app.services.schedule_calendar import shop_now

    if not operation.is_outsourced:
        raise AppError("Only outsourced operations are returned", "NOT_OUTSOURCED", 409)
    if operation.status == OperationStatus.COMPLETED:
        raise AppError("This operation has already returned", "INVALID_TRANSITION", 409)
    if operation.sent_out_date is None:
        raise AppError("Record it as sent out first", "INVALID_TRANSITION", 409)

    day = _parse_shop_date(returned_date, "returnedDate")
    if day > shop_now().date():
        raise AppError("The returned date cannot be in the future", "VALIDATION_ERROR", 400)
    if day < operation.sent_out_date:
        raise AppError(
            "The returned date cannot be before it was sent out", "VALIDATION_ERROR", 400
        )

    ts = _shop_moment(day, 17)
    start = _ensure_utc(operation.actual_start) if operation.actual_start else None
    if start is not None and ts < start:
        ts = start
    job = operation.job_order
    before_status = job.status
    try:
        operation.status = OperationStatus.COMPLETED
        operation.returned_date = day
        operation.actual_end = ts
        db.session.flush()
        job = _reload_job_with_types(job.id)
        job.status = derive_job_status(job)
        advance_part_condition(job)
        _alert_operation_done(operation, job)
        db.session.commit()
        _notify_after_complete(job)
        return operation
    except Exception:
        db.session.rollback()
        raise


def create_rework_operation(operation, user_id, user_role, reason, category=None):
    """Create a follow-on PENDING op; leave the completed original intact."""
    from app.models.operation import ReworkReasonCategory

    check_job_access(operation.job_order, user_id, user_role)
    if user_role != UserRole.ADMIN.value:
        raise AppError("Only the Admin can send an operation for redo", "FORBIDDEN", 403)

    if operation.status != OperationStatus.COMPLETED:
        raise AppError(
            "Only completed operations can be sent for rework", "INVALID_TRANSITION", 409
        )

    cat_raw = (category or "").strip().upper() if category else ""
    if not cat_raw:
        raise AppError("rework reason category is required", "VALIDATION_ERROR", 400)
    try:
        cat = ReworkReasonCategory(cat_raw)
    except ValueError as exc:
        raise AppError("Invalid rework reason category", "VALIDATION_ERROR", 400) from exc

    note = (reason or "").strip() or None
    if cat == ReworkReasonCategory.OTHER and not note:
        raise AppError(
            "note is required when category is Other",
            "VALIDATION_ERROR",
            400,
        )

    job = operation.job_order
    if (
        job.sales_invoice is not None
        or job.status in (JobOrderStatus.FOR_DELIVERY, JobOrderStatus.DELIVERED)
        or getattr(job, "for_delivery_at", None)
        or job.delivered_at
    ):
        raise AppError(
            "This job has been invoiced or set For Delivery, so it can no longer be sent for redo.",
            "INVALID_TRANSITION",
            409,
        )

    redo_seq = operation.sequence_no + 1
    later = [op for op in job.operations if op.sequence_no >= redo_seq]

    try:
        operation.rework_reason = note
        operation.rework_reason_category = cat
        # Shift from the top down so (job, sequence) stays unique at every flush.
        for op in sorted(later, key=lambda o: o.sequence_no, reverse=True):
            op.sequence_no += 1
            db.session.flush()

        redo_worker = _default_redo_worker(operation)
        follow = JobOperation(
            job_order_id=job.id,
            sequence_no=redo_seq,
            operation_name=operation.operation_name,
            operation_type_id=operation.operation_type_id,
            machine_type_id=operation.machine_type_id,
            machine_unit_id=None,
            assigned_worker_id=redo_worker,
            estimated_hours=operation.estimated_hours,
            status=OperationStatus.SCHEDULED if redo_worker else OperationStatus.PENDING,
            rework_of_operation_id=operation.id,
            rework_reason=note,
            rework_reason_category=cat,
        )
        if redo_worker:
            follow.set_helpers(_redo_helpers(operation))
        job.operations.append(follow)
        db.session.flush()
        job.status = derive_job_status(job)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    from app.services.job_order_service import replan_after_redo

    replan_after_redo(job.id, follow.id)
    return follow


def _redo_helpers(original):
    """The original helpers who can still be assigned join the redo."""
    from app.extensions import db
    from app.models.user import User
    from app.services.worker_profile_service import is_assignable_worker

    return [
        wid for wid in original.helper_ids if is_assignable_worker(db.session.get(User, wid))
    ]


def _default_redo_worker(original):
    """The original worker redoes it if they can still take this operation."""
    from app.services.job_order_service import _validate_worker

    if not original.assigned_worker_id:
        return None
    try:
        _validate_worker(original.assigned_worker_id, machine_type_id=original.machine_type_id)
    except AppError:
        return None
    return original.assigned_worker_id


def _assert_unit_not_down(machine_unit_id):
    open_dt = (
        MachineDowntime.query.filter_by(machine_unit_id=machine_unit_id, ended_at=None)
        .order_by(MachineDowntime.started_at.desc())
        .first()
    )
    if open_dt:
        raise AppError(
            "Machine unit is currently down",
            "MACHINE_DOWN",
            409,
        )


def _parse_downtime_category(raw):
    """Accept the enum value or its label (older clients send the label as reason)."""
    from app.models.operation_time import DOWNTIME_CATEGORY_LABELS, DowntimeCategory

    text = str(raw or "").strip()
    if not text:
        raise AppError("breakdown category is required", "VALIDATION_ERROR", 400)
    key = text.upper().replace(" ", "_")
    try:
        return DowntimeCategory(key)
    except ValueError:
        pass
    for cat, label in DOWNTIME_CATEGORY_LABELS.items():
        if label.lower() == text.lower():
            return cat
    raise AppError("Invalid breakdown category", "VALIDATION_ERROR", 400)


def _resolve_downtime_link(unit_id, operation_id, job_order_id, reporter_id, reporter_role):
    if not operation_id:
        if job_order_id and not db.session.get(JobOrder, job_order_id):
            raise AppError("Job order not found", "NOT_FOUND", 404)
        return None, job_order_id or None
    op = db.session.get(JobOperation, operation_id)
    if not op:
        raise AppError("Operation not found", "NOT_FOUND", 404)
    if op.machine_unit_id != unit_id:
        raise AppError(
            "That operation does not run on this machine.",
            "VALIDATION_ERROR",
            400,
        )
    if job_order_id and job_order_id != op.job_order_id:
        raise AppError(
            "jobOrderId does not match the operation's job order.",
            "VALIDATION_ERROR",
            400,
        )
    if reporter_role == UserRole.PRODUCTION_WORKER.value and reporter_id not in op.crew_ids:
        raise AppError(
            "You can only report a breakdown from an operation you are on the crew of.",
            "FORBIDDEN",
            403,
        )
    return op.id, op.job_order_id


def _pause_running_operations_on_unit(machine_unit_id, reported_by_id, ts, received_at=None):
    """Stop the clock on work running on a broken-down unit; workers resume it."""
    running = JobOperation.query.filter_by(
        machine_unit_id=machine_unit_id, status=OperationStatus.IN_PROGRESS
    ).all()
    for op in running:
        last = _last_event(op)
        if not last or last.event not in (
            OperationTimeEvent.START,
            OperationTimeEvent.RESUME,
        ):
            continue
        pause_at = max(ts, _ensure_utc(last.event_at))
        # A crew member who reports it paused it; an Admin or Office report
        # pauses it on the lead's behalf.
        _append_log(
            op,
            reported_by_id if reported_by_id in op.crew_ids else (op.assigned_worker_id or reported_by_id),
            OperationTimeEvent.PAUSE,
            pause_at,
            reason=OperationPauseReason.MACHINE_DOWN,
            received_at=received_at,
        )


_REPAIR_DATE_ROLES = (UserRole.ADMIN.value,)


def _parse_expected_repair_date(raw, started_at):
    """None clears it. Must not be before today or before the breakdown started."""
    from datetime import date

    from app.services.schedule_calendar import shop_now, utc_to_shop

    if raw in (None, ""):
        return None
    try:
        value = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
    except ValueError:
        raise AppError("expectedRepairDate must be a date (YYYY-MM-DD)", "VALIDATION_ERROR", 400)
    floor = max(shop_now().date(), utc_to_shop(_ensure_utc(started_at)).date())
    if value < floor:
        raise AppError(
            "Expected repair date cannot be in the past.",
            "VALIDATION_ERROR",
            400,
        )
    return value


def _alert_single_unit_breakdown(row, unit):
    """The only active unit of its type is down: every job needing that machine
    waits, so tell the Admin once per breakdown."""
    from app.models.machine import MachineUnit
    from app.models.staff_alert import StaffAlertKind
    from app.services.staff_alert_service import raise_alert

    siblings = MachineUnit.query.filter_by(
        machine_type_id=unit.machine_type_id, active=True
    ).count()
    if siblings != 1:
        return
    type_name = unit.machine_type.name if unit.machine_type else "machine"
    until = (
        f"Expected repair: {row.expected_repair_date.strftime('%a %d %b')}."
        if row.expected_repair_date
        else "No expected repair date yet."
    )
    raise_alert(
        roles=[UserRole.ADMIN],
        kind=StaffAlertKind.SINGLE_UNIT_DOWN,
        title=f"{unit.label} is down, the only {type_name} unit",
        message=f"{row.reason}. Every job needing a {type_name} waits until it is repaired. {until}",
        dedupe_key=f"single-unit-down:{row.id}",
    )


def open_machine_downtime(
    machine_unit_id,
    reported_by_id,
    category,
    note=None,
    started_at=None,
    *,
    operation_id=None,
    job_order_id=None,
    reporter_role=None,
    expected_repair_date=None,
    received_at=None,
):
    from app.models.machine import MachineUnit
    from app.models.operation_time import DOWNTIME_CATEGORY_LABELS, DowntimeCategory

    if expected_repair_date not in (None, "") and reporter_role not in _REPAIR_DATE_ROLES:
        raise AppError(
            "Only the Admin can set an expected repair date.",
            "FORBIDDEN",
            403,
        )
    unit = MachineUnit.query.get(machine_unit_id)
    if not unit:
        raise AppError("Machine unit not found", "NOT_FOUND", 404)
    if not unit.active:
        raise AppError(
            "Cannot report downtime on a removed machine. Restore it first, or add a replacement.",
            "CONFLICT",
            409,
        )
    cat = _parse_downtime_category(category)
    note = (str(note).strip() if note else "") or None
    if cat == DowntimeCategory.OTHER and not note:
        raise AppError(
            "note is required when category is Other",
            "VALIDATION_ERROR",
            400,
        )
    linked_op_id, linked_job_id = _resolve_downtime_link(
        machine_unit_id, operation_id, job_order_id, reported_by_id, reporter_role
    )

    existing = MachineDowntime.query.filter_by(
        machine_unit_id=machine_unit_id, ended_at=None
    ).first()
    if existing:
        raise AppError(
            "Machine unit already has an open downtime record",
            "CONFLICT",
            409,
        )

    ts = _parse_timestamp(started_at)
    if received_at is not None:
        check_action_time(
            ts,
            received_at,
            db.session.get(JobOperation, linked_op_id) if linked_op_id else None,
            db.session.get(JobOrder, linked_job_id) if linked_job_id else None,
        )
    repair_date = _parse_expected_repair_date(expected_repair_date, ts)
    try:
        row = MachineDowntime(
            machine_unit_id=machine_unit_id,
            started_at=ts,
            received_at=received_at,
            category=cat,
            reason=DOWNTIME_CATEGORY_LABELS[cat],
            reported_by_id=reported_by_id,
            job_order_id=linked_job_id,
            operation_id=linked_op_id,
            note=note,
            expected_repair_date=repair_date,
        )
        db.session.add(row)
        _pause_running_operations_on_unit(machine_unit_id, reported_by_id, ts, received_at)
        db.session.flush()
        _alert_single_unit_breakdown(row, unit)
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def close_machine_downtime(downtime_id, ended_at=None, note=None, *, actor_id, actor_role):
    row = MachineDowntime.query.get(downtime_id)
    if not row:
        raise AppError("Downtime record not found", "NOT_FOUND", 404)
    if actor_role != UserRole.ADMIN.value:
        raise AppError(
            "Only the Admin can put a machine back in service.",
            "FORBIDDEN",
            403,
        )
    if row.ended_at is not None:
        return row
    ts = _parse_timestamp(ended_at)
    extra = str(note).strip() if note else ""
    try:
        row.ended_at = ts
        if extra:
            row.note = f"{row.note}\nClosed: {extra}".strip() if row.note else extra
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def set_downtime_expected_repair(downtime_id, expected_repair_date):
    row = MachineDowntime.query.get(downtime_id)
    if not row:
        raise AppError("Downtime record not found", "NOT_FOUND", 404)
    if row.ended_at is not None:
        raise AppError("This breakdown is already closed.", "CONFLICT", 409)
    row.expected_repair_date = _parse_expected_repair_date(expected_repair_date, row.started_at)
    db.session.commit()
    return row


_AFFECTED_STATUSES = (
    OperationStatus.PENDING,
    OperationStatus.SCHEDULED,
    OperationStatus.IN_PROGRESS,
    OperationStatus.REWORK,
)


def _serialize_affected_operation(op):
    job = op.job_order
    worker = op.assigned_worker
    year = job.created_at.year if job and job.created_at else datetime.now(timezone.utc).year
    short = (job.id or "")[:4].upper() if job else ""
    return {
        "id": op.id,
        "jobOrderId": op.job_order_id,
        "jobNumber": f"JO-{year}-{short}" if job else None,
        "jobTitle": job.title if job else None,
        "operationName": op.operation_name,
        "status": op.status.value if op.status else None,
        "scheduledStart": op.scheduled_start.isoformat() if op.scheduled_start else None,
        "scheduledEnd": op.scheduled_end.isoformat() if op.scheduled_end else None,
        "assignedWorkerName": worker.full_name if worker else None,
        "assignedWorkerNickname": worker.nickname if worker else None,
        "assignedWorkerPhotoVersion": worker.photo_version if worker else None,
        "crew": op.crew_dicts(),
    }


def list_affected_operations(machine_unit_id):
    rows = (
        JobOperation.query.filter(
            JobOperation.machine_unit_id == machine_unit_id,
            JobOperation.status.in_(_AFFECTED_STATUSES),
        )
        .order_by(JobOperation.scheduled_start.asc(), JobOperation.sequence_no.asc())
        .all()
    )
    return [_serialize_affected_operation(op) for op in rows]


def list_machine_unit_statuses(include_inactive=False):
    from sqlalchemy.orm import joinedload
    from app.models.machine import MachineType, MachineUnit

    query = MachineUnit.query.options(
        joinedload(MachineUnit.machine_type),
        joinedload(MachineUnit.default_operator),
    ).join(MachineType)
    if not include_inactive:
        query = query.filter(MachineUnit.active.is_(True))
    units = query.order_by(MachineType.name, MachineUnit.label).all()
    open_by = {
        row.machine_unit_id: row
        for row in MachineDowntime.query.filter(MachineDowntime.ended_at.is_(None)).all()
    }
    counts = defaultdict(int)
    unit_ids = [u.id for u in units]
    running_by_unit = {}
    next_by_unit = {}
    if unit_ids:
        for op in JobOperation.query.filter(
            JobOperation.machine_unit_id.in_(unit_ids),
            JobOperation.status.in_(_AFFECTED_STATUSES),
        ):
            counts[op.machine_unit_id] += 1

        board_ops = (
            JobOperation.query.options(
                joinedload(JobOperation.job_order),
                joinedload(JobOperation.assigned_worker),
            )
            .filter(
                JobOperation.machine_unit_id.in_(unit_ids),
                JobOperation.status.in_(
                    (OperationStatus.IN_PROGRESS, OperationStatus.SCHEDULED)
                ),
            )
            .order_by(
                JobOperation.machine_unit_id,
                JobOperation.scheduled_start.asc().nullslast(),
                JobOperation.sequence_no.asc(),
            )
            .all()
        )
        for op in board_ops:
            uid = op.machine_unit_id
            if op.status == OperationStatus.IN_PROGRESS:
                running_by_unit[uid] = op
            elif op.status == OperationStatus.SCHEDULED and uid not in next_by_unit:
                next_by_unit[uid] = op

    jobs_today = _jobs_today_by_unit(unit_ids)
    out = []
    for unit in units:
        dt = open_by.get(unit.id)
        payload = unit.to_dict()
        payload["down"] = dt is not None
        payload["openDowntime"] = dt.to_dict() if dt else None
        payload["affectedCount"] = int(counts.get(unit.id, 0))
        running = running_by_unit.get(unit.id)
        nxt = next_by_unit.get(unit.id)
        payload["currentOperation"] = (
            None
            if dt is not None
            else _serialize_affected_operation(running) if running else None
        )
        payload["nextOperation"] = _serialize_affected_operation(nxt) if nxt else None
        payload["jobsToday"] = len(jobs_today.get(unit.id, ()))
        out.append(payload)
    return out


def _jobs_today_by_unit(unit_ids):
    """{unit_id: {job_order_id}} for released jobs with work on the unit today
    (shop time): scheduled to overlap today, or started or finished today."""
    from app.services.schedule_calendar import SHOP_TZ, shop_now

    if not unit_ids:
        return {}
    day_start = datetime.combine(shop_now().date(), datetime.min.time(), tzinfo=SHOP_TZ)
    start = day_start.astimezone(timezone.utc)
    end = (day_start + timedelta(days=1)).astimezone(timezone.utc)
    rows = (
        db.session.query(JobOperation.machine_unit_id, JobOperation.job_order_id)
        .join(JobOrder, JobOperation.job_order_id == JobOrder.id)
        .filter(
            JobOperation.machine_unit_id.in_(unit_ids),
            JobOrder.status.notin_(
                (JobOrderStatus.DRAFT, JobOrderStatus.CANCELLED)
            ),
            db.or_(
                db.and_(
                    JobOperation.scheduled_start < end,
                    JobOperation.scheduled_end > start,
                ),
                db.and_(JobOperation.actual_start >= start, JobOperation.actual_start < end),
                db.and_(JobOperation.actual_end >= start, JobOperation.actual_end < end),
            ),
        )
        .distinct()
        .all()
    )
    out = defaultdict(set)
    for unit_id, job_id in rows:
        out[unit_id].add(job_id)
    return out


def _sync_machine_type_unit_count(machine_type):
    """Keep MachineType.units aligned with active MachineUnit rows."""
    from app.models.machine import MachineUnit

    machine_type.units = (
        MachineUnit.query.filter_by(machine_type_id=machine_type.id, active=True).count()
    )


def _next_machine_unit_label(machine_type):
    import re

    from app.models.machine import MachineUnit

    units = MachineUnit.query.filter_by(machine_type_id=machine_type.id).all()
    nums = []
    for u in units:
        m = re.search(r"#(\d+)\s*$", u.label or "")
        if m:
            nums.append(int(m.group(1)))
    next_n = (max(nums) if nums else len(units)) + 1
    return f"{machine_unit_prefix(machine_type)} #{next_n}"


def create_machine_unit(machine_type_id, label=None):
    """Add a physical unit to a machine type (shop floor replacement / expansion)."""
    from app.models.machine import MachineType, MachineUnit

    if not machine_type_id:
        raise AppError("machineTypeId is required", "VALIDATION_ERROR", 400)
    mt = MachineType.query.get(machine_type_id)
    if not mt:
        raise AppError("Machine type not found", "NOT_FOUND", 404)

    cleaned = (label or "").strip()
    if not cleaned:
        cleaned = _next_machine_unit_label(mt)
    existing = MachineUnit.query.filter_by(machine_type_id=mt.id, label=cleaned).first()
    if existing:
        raise AppError(
            f"A unit named '{cleaned}' already exists for {mt.name}",
            "CONFLICT",
            409,
        )

    unit = MachineUnit(
        machine_type_id=mt.id,
        label=cleaned,
        active=True,
    )
    db.session.add(unit)
    db.session.flush()
    _sync_machine_type_unit_count(mt)
    db.session.commit()
    return unit


def set_machine_unit_active(unit_id, active: bool):
    """
    Soft-remove (retire) or restore a machine unit.
    History on operations/downtime is kept; hard delete is not used.
    """
    from app.models.machine import MachineUnit

    unit = MachineUnit.query.get(unit_id)
    if not unit:
        raise AppError("Machine unit not found", "NOT_FOUND", 404)

    if bool(unit.active) == bool(active):
        return unit

    if not active:
        in_progress = JobOperation.query.filter_by(
            machine_unit_id=unit.id, status=OperationStatus.IN_PROGRESS
        ).first()
        if in_progress:
            raise AppError(
                "Cannot remove a machine that is still running an operation. "
                "Finish or reassign that work first.",
                "CONFLICT",
                409,
            )
        open_dt = MachineDowntime.query.filter_by(
            machine_unit_id=unit.id, ended_at=None
        ).first()
        if open_dt:
            open_dt.ended_at = datetime.now(timezone.utc)
            note = (open_dt.note or "").strip()
            suffix = "Closed when machine was removed from shop floor."
            open_dt.note = f"{note} {suffix}".strip() if note else suffix

    unit.active = bool(active)
    db.session.flush()
    if unit.machine_type:
        _sync_machine_type_unit_count(unit.machine_type)
    db.session.commit()
    return unit


def set_machine_unit_default_operator(unit_id, operator_id):
    """Set or clear the usual operator for a machine unit (null = open to all)."""
    from app.models.machine import MachineUnit
    from app.models.user import User
    from app.services.worker_profile_service import is_assignable_worker

    unit = MachineUnit.query.get(unit_id)
    if not unit:
        raise AppError("Machine unit not found", "NOT_FOUND", 404)

    if operator_id in (None, ""):
        unit.default_operator_id = None
        db.session.commit()
        return unit

    user = User.query.get(operator_id)
    if not user:
        raise AppError("Worker not found", "NOT_FOUND", 404)
    if not is_assignable_worker(user):
        raise AppError(
            "Default operator must be an active production worker",
            "VALIDATION_ERROR",
            400,
        )
    unit.default_operator_id = user.id
    db.session.commit()
    return unit


def downtime_blocked_until(row, now_utc=None):
    """End of the unit's unavailability for an open breakdown: the end of its
    expected repair date, or None (indefinite) when there is no date or the
    date has passed with the machine still down."""
    from datetime import time, timedelta

    from app.services.schedule_calendar import shop_local_to_utc

    if not row.expected_repair_date:
        return None
    until = shop_local_to_utc(row.expected_repair_date + timedelta(days=1), time(0, 0))
    now = _ensure_utc(now_utc or datetime.now(timezone.utc))
    return until if until > now else None


def open_downtimes_by_unit():
    return {
        row.machine_unit_id: row
        for row in MachineDowntime.query.filter(MachineDowntime.ended_at.is_(None)).all()
    }


def open_downtime_intervals_by_unit(now_utc=None):
    """Open downtimes block the unit from started_at through the end of the
    expected repair date, or through a far horizon end when there is none."""
    from datetime import timedelta

    now = _ensure_utc(now_utc or datetime.now(timezone.utc))
    far = now + timedelta(days=3650)
    by_unit = {}
    for unit_id, row in open_downtimes_by_unit().items():
        until = downtime_blocked_until(row, now) or far
        by_unit.setdefault(unit_id, []).append((_ensure_utc(row.started_at), until))
    return by_unit
