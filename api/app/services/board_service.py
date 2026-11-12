"""Read-only production schedule board — segments from existing calendar helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import joinedload

from app.models.job_order import JobOrder, JobOrderStatus
from app.models.machine import MachineType, MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.models.operation_time import MachineDowntime
from app.models.client import Client
from app.services.operation_service import open_downtimes_by_unit
from app.services.schedule_calendar import (
    ensure_utc,
    intersect_intervals,
    merge_intervals,
    serialize_segments,
    shop_break,
    shop_day_windows_union,
    shop_local_to_utc,
    shop_now,
    shop_working_intervals,
    utc_to_shop,
    worker_day_windows,
)
from app.services.schedule_service import (
    clocked_spans,
    compute_schedule_flag,
    operation_working_segments,
)
from app.services.worker_profile_service import query_assignable_workers
from app.utils.errors import AppError

# Same threshold as capacity analytics (running near full).
NEAR_FULL_PCT = 80.0


def _parse_date(value: str | None, label: str) -> date:
    if not value:
        raise AppError(f"{label} is required (YYYY-MM-DD)", "VALIDATION_ERROR", 400)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AppError(f"{label} must be YYYY-MM-DD", "VALIDATION_ERROR", 400) from exc


def _num(v, digits=2):
    if v is None:
        return None
    return round(float(v), digits)


def _job_number(job: JobOrder) -> str:
    year = job.created_at.year if job.created_at else datetime.utcnow().year
    return f"JO-{year}-{(job.id or '')[:4].upper()}"


def _hours(intervals) -> float:
    return sum((e - s).total_seconds() for s, e in intervals) / 3600.0


def _floor_flags(op, now, open_downs) -> dict:
    """Stalled (paused over a working day) and booked-on-a-down-unit flags for the board."""
    from app.services.operation_service import downtime_blocked_until
    from app.services.schedule_service import paused_at, stalled_since

    stalled = stalled_since(op, now) if op.status == OperationStatus.IN_PROGRESS else None
    paused = paused_at(op) if op.status == OperationStatus.IN_PROGRESS else None
    down = open_downs.get(op.machine_unit_id) if op.machine_unit_id else None
    on_down_unit = False
    if down is not None and op.status != OperationStatus.COMPLETED and op.scheduled_end:
        until = downtime_blocked_until(down, now)
        on_down_unit = ensure_utc(op.scheduled_end) > ensure_utc(down.started_at) and (
            until is None or ensure_utc(op.scheduled_start) < until
        )
    return {
        "pausedAt": paused.isoformat() if paused else None,
        "stalled": stalled is not None,
        "stalledSince": stalled.isoformat() if stalled else None,
        "onDownUnit": on_down_unit,
        "downtimeReason": down.reason if on_down_unit else None,
    }


def add_booked_hours(op, working, start_utc, end_utc, now, booked_by_type, worked_by_type) -> None:
    """Add an unfinished machine operation's working time inside the period, and
    the part of it already worked, to its machine type."""
    if not op.machine_type_id or op.status == OperationStatus.COMPLETED:
        return
    inside = intersect_intervals(working, [(start_utc, end_utc)])
    booked_by_type[op.machine_type_id] += _hours(inside)
    worked_by_type[op.machine_type_id] += _hours(
        intersect_intervals(inside, merge_intervals(clocked_spans(op, now)))
    )


def capacity_for_period(start_utc, end_utc, now=None) -> list[dict]:
    """``machine_capacity`` for every released, unfinished machine operation
    booked inside [start_utc, end_utc)."""
    now = now or datetime.now(timezone.utc)
    ops = (
        JobOperation.query.join(JobOrder)
        .filter(
            JobOrder.status.notin_(
                (JobOrderStatus.DRAFT, JobOrderStatus.CANCELLED)
            ),
            JobOperation.status != OperationStatus.COMPLETED,
            JobOperation.machine_type_id.isnot(None),
            JobOperation.scheduled_start.isnot(None),
            JobOperation.scheduled_end.isnot(None),
            JobOperation.scheduled_start < end_utc,
            (JobOperation.scheduled_end > start_utc)
            | (JobOperation.status == OperationStatus.IN_PROGRESS),
        )
        .all()
    )
    booked, worked = defaultdict(float), defaultdict(float)
    for op in ops:
        add_booked_hours(op, operation_working_segments(op, now), start_utc, end_utc, now, booked, worked)
    units = MachineUnit.query.filter_by(active=True).all()
    downtimes = MachineDowntime.query.filter(
        MachineDowntime.started_at < end_utc,
        (MachineDowntime.ended_at.is_(None)) | (MachineDowntime.ended_at > start_utc),
    ).all()
    return machine_capacity(units, downtimes, booked, worked, start_utc, end_utc)


def machine_capacity(units, downtimes, booked_by_type, worked_by_type, start_utc, end_utc) -> list[dict]:
    """Per machine type: hours booked inside the period less hours already worked,
    over the shop's working hours (with overtime and holidays) on the units
    while they are not down. Units down the whole period add nothing."""
    from app.services.operation_service import downtime_blocked_until

    shop = shop_working_intervals(start_utc, end_utc)
    shop_hours = _hours(shop)
    down_by_unit = defaultdict(list)
    for d in downtimes:
        until = d.ended_at or downtime_blocked_until(d) or end_utc
        down_by_unit[d.machine_unit_id].append((ensure_utc(d.started_at), ensure_utc(until)))
    by_type = defaultdict(list)
    for u in units:
        by_type[u.machine_type_id].append(u)

    rows = []
    for mt in MachineType.query.order_by(MachineType.code).all():
        type_units = by_type.get(mt.id) or []
        if not type_units:
            continue
        available = 0.0
        units_down = 0
        for u in type_units:
            lost = _hours(intersect_intervals(shop, merge_intervals(down_by_unit.get(u.id, []))))
            available += max(shop_hours - lost, 0.0)
            if shop_hours and lost >= shop_hours:
                units_down += 1
        booked = booked_by_type.get(mt.id, 0.0)
        worked = worked_by_type.get(mt.id, 0.0)
        load = max(booked - worked, 0.0)
        rows.append(
            {
                "machineTypeId": mt.id,
                "machineTypeCode": mt.code,
                "machineTypeName": mt.name,
                "bookedHours": _num(booked, 1),
                "workedHours": _num(worked, 1),
                "loadHours": _num(load, 1),
                "availableHours": _num(available, 1),
                "units": len(type_units),
                "unitsDown": units_down,
                "loadPct": _num(load / available * 100.0, 1) if available > 0 else (100.0 if load else 0.0),
            }
        )
    return rows


def schedule_board(
    from_s: str | None = None,
    to_s: str | None = None,
    machine_type_id: str | None = None,
    worker_id: str | None = None,
    client_id: str | None = None,
    include_completed: bool = True,
    for_worker: bool = False,
):
    """
    Scheduled operations overlapping [from, to] (shop-local dates), with
    derived working segments (never overnight wall-clock blocks).
    """
    from sqlalchemy.orm import selectinload

    from app.models.material_purchase import MaterialPurchase
    from app.services.material_purchase_service import material_wait_fields
    period_from = _parse_date(from_s, "from")
    period_to = _parse_date(to_s, "to")
    if period_to < period_from:
        raise AppError("to must be on or after from", "VALIDATION_ERROR", 400)

    start_utc = shop_local_to_utc(period_from, time(0, 0))
    end_utc = shop_local_to_utc(period_to + timedelta(days=1), time(0, 0))

    q = (
        JobOperation.query.options(
            joinedload(JobOperation.job_order).joinedload(JobOrder.client),
            joinedload(JobOperation.job_order).selectinload(JobOrder.operations),
            joinedload(JobOperation.job_order)
            .selectinload(JobOrder.material_purchases)
            .joinedload(MaterialPurchase.supplier_order),
            joinedload(JobOperation.machine_type),
            joinedload(JobOperation.machine_unit),
            joinedload(JobOperation.assigned_worker),
            joinedload(JobOperation.operation_type),
        )
        .join(JobOrder)
        .filter(
            JobOperation.scheduled_start.isnot(None),
            JobOperation.scheduled_end.isnot(None),
            JobOperation.scheduled_start < end_utc,
            # In-progress work stays booked past its planned end until it is done.
            (JobOperation.scheduled_end > start_utc)
            | (JobOperation.status == OperationStatus.IN_PROGRESS),
        )
    )
    if not include_completed:
        q = q.filter(JobOperation.status != OperationStatus.COMPLETED)
    if machine_type_id:
        q = q.filter(JobOperation.machine_type_id == machine_type_id)
    if worker_id:
        q = q.filter(JobOperation.crew_includes(worker_id))
    if client_id:
        q = q.filter(JobOrder.client_id == client_id)

    from app.models.job_order import JobOrderStatus

    q = q.filter(
        JobOrder.status.notin_((JobOrderStatus.DRAFT, JobOrderStatus.CANCELLED))
    )

    ops = q.order_by(JobOperation.scheduled_start.asc()).all()

    operations_out = []
    booked_by_type = defaultdict(float)
    worked_by_type = defaultdict(float)
    jobs_meta = {}  # job_id -> {due, projected, flag, number, title}
    now = datetime.now(timezone.utc)
    open_downs = open_downtimes_by_unit()

    for op in ops:
        job = op.job_order
        working = operation_working_segments(op, now)
        segments = serialize_segments(working)
        # Clip to board window for payload size; keep full scheduled window for hover.
        clipped = []
        for seg in segments:
            s = max(datetime.fromisoformat(seg["start"].replace("Z", "+00:00")), start_utc)
            e = min(datetime.fromisoformat(seg["end"].replace("Z", "+00:00")), end_utc)
            if e > s:
                clipped.append({"start": s.isoformat(), "end": e.isoformat()})
        if not clipped and op.status == OperationStatus.IN_PROGRESS:
            continue

        projected = None
        schedule_flag = None
        is_late = False
        if job:
            jid = job.id
            if jid not in jobs_meta:
                ends = [o.scheduled_end for o in (job.operations or []) if o.scheduled_end]
                # Prefer already-loaded ops; fall back to this op only
                if not ends and op.scheduled_end:
                    ends = [op.scheduled_end]
                # Collect max end across board ops for this job; refine below
                jobs_meta[jid] = {
                    "due": job.due_date,
                    "projected": max(ends) if ends else None,
                    "number": _job_number(job),
                    "title": job.title,
                    "status": job.status.value if job.status else None,
                    "materialWait": material_wait_fields(job, for_worker=for_worker),
                }
            else:
                if op.scheduled_end and (
                    jobs_meta[jid]["projected"] is None
                    or op.scheduled_end > jobs_meta[jid]["projected"]
                ):
                    jobs_meta[jid]["projected"] = op.scheduled_end

            projected = jobs_meta[jid]["projected"]
            schedule_flag = compute_schedule_flag(projected, job.due_date)
            is_late = schedule_flag in ("AMBER", "RED")
            jobs_meta[jid]["flag"] = schedule_flag

        add_booked_hours(op, working, start_utc, end_utc, now, booked_by_type, worked_by_type)

        operations_out.append(
            {
                "id": op.id,
                "jobOrderId": op.job_order_id,
                "jobNumber": _job_number(job) if job else None,
                "jobTitle": job.title if job else None,
                "jobStatus": job.status.value if job and job.status else None,
                "clientId": job.client_id if job else None,
                "clientName": job.client.name if job and job.client else None,
                "sequenceNo": op.sequence_no,
                "operationName": op.operation_name,
                "status": op.status.value if op.status else None,
                "estimatedHours": _num(op.estimated_hours),
                "scheduledStart": op.scheduled_start.isoformat() if op.scheduled_start else None,
                "scheduledEnd": op.scheduled_end.isoformat() if op.scheduled_end else None,
                "segments": clipped,
                "machineTypeId": op.machine_type_id,
                "machineTypeCode": op.machine_type.code if op.machine_type else None,
                "machineTypeName": op.machine_type.name if op.machine_type else None,
                "machineUnitId": op.machine_unit_id,
                "machineUnitLabel": op.machine_unit.label if op.machine_unit else None,
                "assignedWorkerId": op.assigned_worker_id,
                "assignedWorkerName": (
                    op.assigned_worker.full_name if op.assigned_worker else None
                ),
                "assignedWorkerNickname": (
                    op.assigned_worker.nickname if op.assigned_worker else None
                ),
                "assignedWorkerPhotoVersion": (
                    op.assigned_worker.photo_version if op.assigned_worker else None
                ),
                "helperIds": op.helper_ids,
                "crew": op.crew_dicts(),
                "dueDate": job.due_date.isoformat() if job and job.due_date else None,
                "projectedCompletion": projected.isoformat() if projected else None,
                "scheduleFlag": schedule_flag,
                "isLate": is_late,
                "scheduleColor": job.board_color if job else None,
                **(jobs_meta[job.id]["materialWait"] if job else {}),
                **_floor_flags(op, now, open_downs),
            }
        )

    # Refine projected completion using all scheduled ends on each job (not only filtered ops)
    job_ids = list(jobs_meta.keys())
    if job_ids:
        all_ends = (
            JobOperation.query.filter(
                JobOperation.job_order_id.in_(job_ids),
                JobOperation.scheduled_end.isnot(None),
            )
            .with_entities(JobOperation.job_order_id, JobOperation.scheduled_end)
            .all()
        )
        max_by_job = {}
        for jid, end in all_ends:
            if jid not in max_by_job or end > max_by_job[jid]:
                max_by_job[jid] = end
        for jid, meta in jobs_meta.items():
            if jid in max_by_job:
                meta["projected"] = max_by_job[jid]
                meta["flag"] = compute_schedule_flag(meta["projected"], meta["due"])
        for row in operations_out:
            jid = row["jobOrderId"]
            if jid in jobs_meta:
                proj = jobs_meta[jid]["projected"]
                flag = jobs_meta[jid].get("flag")
                row["projectedCompletion"] = proj.isoformat() if proj else None
                row["scheduleFlag"] = flag
                row["isLate"] = flag in ("AMBER", "RED")

    units = (
        MachineUnit.query.options(joinedload(MachineUnit.machine_type))
        .filter_by(active=True)
        .order_by(MachineUnit.label)
        .all()
    )
    if machine_type_id:
        units = [u for u in units if u.machine_type_id == machine_type_id]

    machine_units_out = [
        {
            "id": u.id,
            "label": u.label,
            "machineTypeId": u.machine_type_id,
            "machineTypeCode": u.machine_type.code if u.machine_type else None,
            "machineTypeName": u.machine_type.name if u.machine_type else None,
        }
        for u in units
    ]

    workers = (
        query_assignable_workers()
        .all()
    )
    workers_out = [
        {
            "id": w.id,
            "fullName": w.full_name,
            "nickname": w.nickname,
            "role": w.role.value,
            # A production worker only sees other people's photos on their own jobs.
            "photoVersion": (
                w.photo_version if not for_worker or w.id == worker_id else None
            ),
        }
        for w in workers
    ]

    clients_out = [
        {"id": c.id, "name": c.name}
        for c in Client.query.order_by(Client.name).all()
    ]

    dts = (
        MachineDowntime.query.options(joinedload(MachineDowntime.machine_unit))
        .filter(
            MachineDowntime.started_at < end_utc,
            (MachineDowntime.ended_at.is_(None)) | (MachineDowntime.ended_at > start_utc),
        )
        .all()
    )
    if machine_type_id:
        unit_ids = {u.id for u in units}
        dts = [d for d in dts if d.machine_unit_id in unit_ids]

    far_end = end_utc
    downtimes_out = []
    for d in dts:
        d_end = d.ended_at or far_end
        s = max(d.started_at, start_utc) if d.started_at else start_utc
        e = min(d_end, end_utc)
        if e <= s:
            continue
        downtimes_out.append(
            {
                "id": d.id,
                "machineUnitId": d.machine_unit_id,
                "machineUnitLabel": d.machine_unit.label if d.machine_unit else None,
                "startedAt": d.started_at.isoformat() if d.started_at else None,
                "endedAt": d.ended_at.isoformat() if d.ended_at else None,
                "segmentStart": s.isoformat(),
                "segmentEnd": e.isoformat(),
                "reason": d.reason,
                "open": d.ended_at is None,
            }
        )

    capacity = machine_capacity(units, dts, booked_by_type, worked_by_type, start_utc, end_utc)
    if machine_type_id:
        capacity = [c for c in capacity if c["machineTypeId"] == machine_type_id]
    near_full = [
        {**c, "projectedLoadPct": c["loadPct"]} for c in capacity if c["loadPct"] >= NEAR_FULL_PCT
    ]

    at_risk = []
    seen_jobs = set()
    for row in operations_out:
        jid = row["jobOrderId"]
        if jid in seen_jobs:
            continue
        if row.get("isLate") and row.get("jobStatus") not in (
            JobOrderStatus.COMPLETED.value,
            JobOrderStatus.FOR_DELIVERY.value,
            JobOrderStatus.DELIVERED.value,
        ):
            seen_jobs.add(jid)
            at_risk.append(
                {
                    "jobOrderId": jid,
                    "jobNumber": row["jobNumber"],
                    "jobTitle": row["jobTitle"],
                    "dueDate": row["dueDate"],
                    "projectedCompletion": row["projectedCompletion"],
                    "scheduleFlag": row["scheduleFlag"],
                }
            )

    return {
        "period": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "timezone": "Asia/Manila",
        "shopDayWindows": shop_day_windows_union(period_from, period_to),
        "workerDayWindows": (
            worker_day_windows(worker_id, period_from, period_to) if worker_id else None
        ),
        "shopBreak": {
            "start": shop_break()[0].strftime("%H:%M"),
            "end": shop_break()[1].strftime("%H:%M"),
        },
        "machineUnits": machine_units_out,
        "workers": workers_out,
        "clients": clients_out,
        "operations": operations_out,
        "downtimes": downtimes_out,
        "summary": {
            "operationsScheduled": len(operations_out),
            "jobsScheduled": len({row["jobOrderId"] for row in operations_out}),
            "machinesNearFullCapacity": near_full,
            "machineCapacity": capacity,
            "jobsAtRisk": at_risk,
        },
    }
