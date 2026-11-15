"""Scheduling scenarios run end to end through the real propose, confirm, apply,
redo and overdue-delivery code: bookings and clashes, working time, machines,
materials, edits, and a load run.

Uses the bmsc_test database from conftest (schema built from the models).
"""

import threading
import time as clock
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from tests.catalog_helpers import approve_issue, ensure_material
from app.extensions import db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.machine import MachineType, MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.models.operation_time import DowntimeCategory, MachineDowntime
from app.models.schedule_move import DelayKind
from app.models.staff_alert import StaffAlert, StaffAlertKind
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder
from app.models.user import User, UserRole, UserStatus
from app.models.worker_profile import WorkerProfile
from app.models.worker_skill import (
    CalendarExceptionType,
    OperationType,
    WorkCalendarException,
    WorkerSchedule,
    WorkerSkill,
)
from app.services.overdue_delivery_service import check_overdue_deliveries
from app.services import job_order_service
from app.services import material_delay_service as delay_service
from app.services.auth_service import update_user
from app.services.worker_profile_service import ensure_worker_profile
from app.services.schedule_calendar import ensure_utc, shop_local_to_utc, shop_now
from app.services.schedule_service import operation_working_segments, place_from_start

_today = shop_now().date()
# A Monday one to two weeks out: inside the proposal horizon, never in the past.
MON = _today + timedelta(days=(7 - _today.weekday()) % 7 + 7)
TUE = MON + timedelta(days=1)
LAST_SAT = _today - timedelta(days=(_today.weekday() - 5) % 7 or 7)  # always in the past


def _at(d, hh, mm=0):
    return shop_local_to_utc(d, time(hh, mm))


def _iso(d, hh, mm=0):
    return _at(d, hh, mm).isoformat()


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _next_quarter_hour():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return now + timedelta(minutes=15 - now.minute % 15)


def _user(email, role, name, hours=(time(8, 0), time(17, 0))):
    user = User(
        email=email, password_hash="x", full_name=name, role=role,
        status=UserStatus.ACTIVE, active=True,
    )
    db.session.add(user)
    db.session.flush()
    if role == UserRole.PRODUCTION_WORKER:
        db.session.add(WorkerProfile(user_id=user.id))
        for dow in range(6):
            db.session.add(
                WorkerSchedule(
                    worker_id=user.id, day_of_week=dow, is_working=True,
                    start_time=hours[0], end_time=hours[1],
                )
            )
    return user


def _headers(user):
    token = create_access_token(identity=user.id, additional_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def shop(app):
    admin = _user("sc_admin@test.local", UserRole.ADMIN, "Sc Admin")
    office = _user("sc_office@test.local", UserRole.OFFICE_STAFF, "Sc Office")
    people = {
        key: _user(f"sc_{key}@test.local", UserRole.PRODUCTION_WORKER, name)
        for key, name in (
            ("rina", "Rina"), ("omar", "Omar"), ("pia", "Pia"), ("gio", "Gio"), ("ben", "Ben"),
        )
    }
    people["lara"] = _user(
        "sc_lara@test.local", UserRole.PRODUCTION_WORKER, "Lara", hours=(time(10, 0), time(19, 0))
    )

    laser = MachineType(code="LASER_S", name="Laser", units=1)
    shaper = MachineType(code="SHAPER_S", name="Shaper", units=1)
    lathe = MachineType(code="LATHE_S", name="Lathe", units=2)
    db.session.add_all([laser, shaper, lathe])
    db.session.flush()
    units = {
        "laser1": MachineUnit(machine_type_id=laser.id, label="Laser #1", active=True),
        "shaper1": MachineUnit(machine_type_id=shaper.id, label="Shaper #1", active=True),
        "lathe2": MachineUnit(
            machine_type_id=lathe.id, label="Lathe #2", active=True,
            default_operator_id=people["gio"].id,
        ),
        "lathe8": MachineUnit(machine_type_id=lathe.id, label="Lathe #8", active=True),
    }
    turning = OperationType(code="TURNING_S", name="Turning", default_machine_type_id=lathe.id)
    heat = OperationType(
        code="HEAT_S", name="Heat Treatment", is_outsourced=True, default_turnaround_days=3
    )
    client_row = Client(name="Scenario Client")
    supplier = Supplier(name="Scenario Steel", code="SCS", typical_lead_time_days=5)
    db.session.add_all([*units.values(), turning, heat, client_row, supplier])
    for person, mtype in (
        ("rina", laser), ("omar", laser), ("rina", lathe), ("gio", lathe), ("ben", lathe),
        ("ben", shaper), ("omar", shaper),
    ):
        db.session.add(WorkerSkill(worker_id=people[person].id, machine_type_id=mtype.id, proficiency=3))
    db.session.commit()
    return {
        "admin": admin, "office": office, **people, **units,
        "laser": laser, "shaper": shaper, "lathe": lathe,
        "turning": turning, "heat": heat, "client": client_row, "supplier": supplier,
    }


def _job(shop, status=JobOrderStatus.DRAFT, material=MaterialStatus.NOT_REQUIRED):
    job = JobOrder(
        client_id=shop["client"].id, title="Scenario Job", due_date=MON + timedelta(days=40),
        status=status, job_type=JobType.FABRICATION, part_condition=PartCondition.RAW_MATERIAL,
        material_status=material, created_by_id=shop["admin"].id,
    )
    db.session.add(job)
    db.session.commit()
    return job


def _booked(job, seq, lead, start, end, *, unit=None, helpers=(), hours=None,
            status=OperationStatus.SCHEDULED, actual_start=None):
    op = JobOperation(
        job_order_id=job.id, sequence_no=seq, operation_name=f"Booked {seq}",
        assigned_worker_id=lead.id,
        estimated_hours=Decimal(str(hours or (end - start).total_seconds() / 3600)),
        machine_type_id=unit.machine_type_id if unit else None,
        machine_unit_id=unit.id if unit else None,
        scheduled_start=start, scheduled_end=end, status=status, actual_start=actual_start,
    )
    op.set_helpers([h.id for h in helpers])
    db.session.add(op)
    db.session.commit()
    return op


def _op(lead, seq=1, hours=2, *, machine=None, helpers=(), start=None, unit=None, type_=None):
    return {
        "sequenceNo": seq,
        "operationName": type_.name if type_ else f"Step {seq}",
        "operationTypeId": type_.id if type_ else None,
        "assignedWorkerId": lead.id if lead else None,
        "helperIds": [h.id for h in helpers],
        "estimatedHours": hours,
        "machineTypeId": machine.id if machine else None,
        "machineUnitId": unit.id if unit else None,
        "scheduledStart": start,
    }


def _propose(client, shop, job, ops=None, anchor=None, **extra):
    body = {"anchor": (anchor or _at(MON, 8)).isoformat(), **extra}
    if ops is not None:
        body["operations"] = ops
    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/propose", json=body, headers=_headers(shop["admin"])
    )
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _merged(ops, proposal):
    """As the planning page does: the operations with the proposed start and unit."""
    return [
        {**o, "scheduledStart": p["scheduledStart"], "machineUnitId": p["machineUnitId"] or o["machineUnitId"]}
        for o, p in zip(ops, proposal["operations"])
    ]


def _confirm(client, shop, job, ops, proposal=None):
    return client.post(
        f"/api/v1/job-orders/{job.id}/schedule/confirm",
        json={"operations": _merged(ops, proposal) if proposal else ops},
        headers=_headers(shop["admin"]),
    )


def _refused(res, text):
    assert res.status_code == 409, res.get_json()
    msg = res.get_json()["error"]["message"]
    assert text in msg, msg
    return msg


def _starts(proposal):
    return [_parse(o["scheduledStart"]) if o["scheduledStart"] else None for o in proposal["operations"]]


def _double_bookings():
    """Every pair of released operations sharing a worker or a unit at the same working time."""
    ops = [
        op
        for op in JobOperation.query.join(JobOrder).filter(
            JobOrder.status != JobOrderStatus.DRAFT,
            JobOperation.scheduled_start.isnot(None),
            JobOperation.scheduled_end.isnot(None),
        )
        if not op.is_outsourced
    ]
    segs = {op.id: operation_working_segments(op) for op in ops}
    clashes = []
    for i, a in enumerate(ops):
        for b in ops[i + 1:]:
            shared = set(a.crew_ids) & set(b.crew_ids)
            same_unit = a.machine_unit_id and a.machine_unit_id == b.machine_unit_id
            if not shared and not same_unit:
                continue
            if any(s < be and bs < e for s, e in segs[a.id] for bs, be in segs[b.id]):
                clashes.append((a.job_order.job_number, a.sequence_no, b.job_order.job_number, b.sequence_no))
    return clashes


# ---- Bookings and clashes ----------------------------------------------------


def test_two_pending_jobs_same_worker_second_confirm_refused_then_replaced(client, shop):
    a, b = _job(shop), _job(shop)
    ops = [_op(shop["rina"], hours=2)]
    pa, pb = _propose(client, shop, a, ops), _propose(client, shop, b, ops)
    assert _starts(pa) == _starts(pb) == [_at(MON, 8)]

    assert _confirm(client, shop, a, ops, pa).status_code == 200
    _refused(_confirm(client, shop, b, ops, pb), f"Rina is already booked on {a.job_number}")
    assert db.session.get(JobOrder, b.id).status == JobOrderStatus.DRAFT

    pb = _propose(client, shop, b, ops)
    assert _starts(pb) == [_at(MON, 10)]
    assert _confirm(client, shop, b, ops, pb).status_code == 200
    assert _double_bookings() == []


def test_clash_on_a_helper_not_the_lead(client, shop):
    a, b = _job(shop), _job(shop)
    ops_a = [_op(shop["omar"], hours=2, helpers=[shop["pia"]])]
    ops_b = [_op(shop["rina"], hours=2, helpers=[shop["pia"]])]
    pa, pb = _propose(client, shop, a, ops_a), _propose(client, shop, b, ops_b)
    assert _confirm(client, shop, a, ops_a, pa).status_code == 200
    _refused(_confirm(client, shop, b, ops_b, pb), "Pia is already booked")

    pb = _propose(client, shop, b, ops_b)
    assert _starts(pb) == [_at(MON, 10)]
    assert _confirm(client, shop, b, ops_b, pb).status_code == 200
    assert _double_bookings() == []


def test_one_laser_unit_two_jobs_same_morning(client, shop):
    a, b = _job(shop), _job(shop)
    ops_a = [_op(shop["rina"], hours=3, machine=shop["laser"])]
    ops_b = [_op(shop["omar"], hours=3, machine=shop["laser"])]
    pa, pb = _propose(client, shop, a, ops_a), _propose(client, shop, b, ops_b)
    assert pa["operations"][0]["machineUnitId"] == pb["operations"][0]["machineUnitId"] == shop["laser1"].id
    assert _confirm(client, shop, a, ops_a, pa).status_code == 200
    _refused(_confirm(client, shop, b, ops_b, pb), "Laser #1 is already booked")

    pb = _propose(client, shop, b, ops_b)
    assert _starts(pb) == [_at(MON, 11)]
    assert _confirm(client, shop, b, ops_b, pb).status_code == 200
    assert _double_bookings() == []


def test_shaper_already_in_progress(client, shop):
    busy = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    _booked(busy, 1, shop["ben"], _at(MON, 8), _at(MON, 12), unit=shop["shaper1"],
            status=OperationStatus.IN_PROGRESS, actual_start=_at(MON, 8))
    b = _job(shop)
    ops = [_op(shop["omar"], hours=2, machine=shop["shaper"])]
    pb = _propose(client, shop, b, ops)
    assert _starts(pb) == [_at(MON, 13)]
    assert _confirm(client, shop, b, ops, pb).status_code == 200


def test_stale_proposal_caught_on_the_second_operation(client, shop):
    a, b = _job(shop), _job(shop)
    ops_a = [_op(shop["omar"], 1, 2), _op(shop["rina"], 2, 2)]
    ops_b = [_op(shop["ben"], 1, 2), _op(shop["rina"], 2, 2)]
    pa, pb = _propose(client, shop, a, ops_a), _propose(client, shop, b, ops_b)
    assert _starts(pa)[1] == _starts(pb)[1] == _at(MON, 10)
    assert _confirm(client, shop, a, ops_a, pa).status_code == 200
    msg = _refused(_confirm(client, shop, b, ops_b, pb), "Rina is already booked")
    assert "#2" in msg
    pb = _propose(client, shop, b, ops_b)
    assert _starts(pb)[1] == _at(MON, 13)
    assert _confirm(client, shop, b, ops_b, pb).status_code == 200
    assert _double_bookings() == []


def test_lead_on_one_job_helper_on_another(client, shop):
    a = _job(shop)
    ops_a = [_op(shop["rina"], hours=2)]
    assert _confirm(client, shop, a, ops_a, _propose(client, shop, a, ops_a)).status_code == 200

    b = _job(shop)
    ops_b = [_op(shop["omar"], hours=2, helpers=[shop["rina"]], start=_iso(MON, 9))]
    pinned = _propose(client, shop, b, ops_b, pinSequence=1, lockBeforeSequence=1)
    assert [p["code"] for p in pinned["problems"]] == ["WORKER_CONFLICT"]
    _refused(_confirm(client, shop, b, ops_b, pinned), "Rina is already booked")

    fresh = _propose(client, shop, b, [{**ops_b[0], "scheduledStart": None}])
    assert _starts(fresh) == [_at(MON, 10)]


def test_busy_lathe_operator_unit_goes_to_another_worker_with_reason(client, shop):
    window = {"scheduledStart": _iso(MON, 8), "scheduledEnd": _iso(MON, 10)}
    other = _job(shop, status=JobOrderStatus.SCHEDULED)
    _booked(other, 1, shop["gio"], _at(MON, 8), _at(MON, 10))
    _booked(other, 2, shop["ben"], _at(MON, 8), _at(MON, 10), unit=shop["lathe8"])
    res = client.post(
        "/api/v1/workers/suggest",
        json={"operationTypeId": shop["turning"].id, "machineTypeId": shop["lathe"].id, **window},
        headers=_headers(shop["admin"]),
    )
    rows = {s["fullName"]: s for s in res.get_json()["suggestions"]}
    assert "Gio" not in rows and "Ben" not in rows
    assert rows["Rina"]["machineUnitLabel"] == "Lathe #2"
    assert rows["Rina"]["reason"].startswith("Lathe #2, whose operator is not free")


# ---- Working time ------------------------------------------------------------


def test_eight_hours_from_1100_cross_lunch_and_end_next_day(client, shop):
    start, end, segments = place_from_start(shop["rina"].id, _at(MON, 11), 8)
    assert (start, end) == (_at(MON, 11), _at(TUE, 11))
    assert segments == [(_at(MON, 11), _at(MON, 12)), (_at(MON, 13), _at(MON, 17)), (_at(TUE, 8), _at(TUE, 11))]
    job = _job(shop)
    p = _propose(client, shop, job, [_op(shop["rina"], hours=8, start=_iso(MON, 11))],
                 pinSequence=1, lockBeforeSequence=1)
    assert _parse(p["operations"][0]["scheduledEnd"]) == _at(TUE, 11)


def test_crew_with_different_hours_works_only_when_all_are_on_shift(client, shop):
    job = _job(shop)
    ops = [_op(shop["rina"], hours=8, helpers=[shop["lara"]])]
    p = _propose(client, shop, job, ops)
    op = p["operations"][0]
    # Rina 08-17, Lara 10-19: together 10-17 less the break.
    assert _parse(op["scheduledStart"]) == _at(MON, 10)
    assert _parse(op["scheduledEnd"]) == _at(TUE, 12)
    assert op["segments"][0]["start"] == _iso(MON, 10)
    assert _confirm(client, shop, job, ops, p).status_code == 200


# ---- Materials ---------------------------------------------------------------


def _order_line(client, shop, job, issued, expected):
    res = client.post(
        "/api/v1/supplier-orders/draft-lines",
        json={"supplierId": shop["supplier"].id, "lines": [
            {"jobOrderId": job.id, "materialCatalogId": ensure_material("Plate"), "materialName": "Plate", "unit": "pcs", "quantity": 1, "unitCost": 100}
        ]},
        headers=_headers(shop["office"]),
    )
    assert res.status_code == 200, res.get_json()
    order_id = res.get_json()["id"]
    res = approve_issue(client, f"/api/v1/supplier-orders/{order_id}", {"dateIssued": issued.isoformat()}, _headers(shop["office"]))
    assert res.status_code == 200, res.get_json()
    so = db.session.get(SupplierOrder, order_id)
    so.expected_delivery_date = expected
    so.original_expected_delivery_date = expected
    db.session.commit()
    return so


def test_overdue_delivery_moves_job_as_material_without_clashing(client, shop):
    today = _today
    tomorrow = today + timedelta(days=1)
    while tomorrow.weekday() == 6:
        tomorrow += timedelta(days=1)
    other = _job(shop, status=JobOrderStatus.SCHEDULED)
    _booked(other, 1, shop["rina"], _at(tomorrow, 8), _at(tomorrow, 12))

    job = _job(shop, status=JobOrderStatus.SCHEDULED, material=MaterialStatus.TO_ORDER)
    far = today + timedelta(days=30)
    first = _booked(job, 1, shop["rina"], _at(far, 8), _at(far, 10))
    _booked(job, 2, shop["omar"], _at(far, 13), _at(far, 15))
    _order_line(client, shop, job, today - timedelta(days=5), today - timedelta(days=2))
    # The job is planned for today, before the overdue material can arrive.
    for op, hh in ((first, 8), (job.operations[1], 13)):
        op.scheduled_start, op.scheduled_end = _at(today, hh), _at(today, hh + 2)
    db.session.commit()

    result = check_overdue_deliveries()
    assert [o["jobNumber"] for o in result["movedJobs"]] == [job.job_number]
    db.session.expire_all()
    job = db.session.get(JobOrder, job.id)
    assert job.delay_kind == DelayKind.MATERIAL
    starts = sorted(ensure_utc(o.scheduled_start) for o in job.operations)
    assert starts[0] >= _at(tomorrow, 13)
    assert _double_bookings() == []


def test_material_move_into_another_jobs_booking_goes_after_it(client, shop):
    other = _job(shop, status=JobOrderStatus.SCHEDULED)
    _booked(other, 1, shop["rina"], _at(MON, 8), _at(MON, 17), hours=8)

    job = _job(shop, status=JobOrderStatus.SCHEDULED, material=MaterialStatus.TO_ORDER)
    _booked(job, 1, shop["rina"], _at(MON - timedelta(days=4), 8), _at(MON - timedelta(days=4), 10))
    so = _order_line(client, shop, job, _today, MON)
    outcomes = delay_service.reschedule_for_order(so, delay_service.EXPECTED_DATE_CHANGED)
    assert [o["outcome"] for o in outcomes] in (["MOVED"], [])
    db.session.expire_all()
    op = db.session.get(JobOrder, job.id).operations[0]
    assert ensure_utc(op.scheduled_start) == _at(TUE, 8)
    other_op = db.session.get(JobOrder, other.id).operations[0]
    assert ensure_utc(other_op.scheduled_start) == _at(MON, 8)
    assert _double_bookings() == []


# ---- Edits and changes -------------------------------------------------------


def test_typed_start_in_the_past_or_on_a_holiday(client, shop):
    job = _job(shop)
    past = _today - timedelta(days=7)
    while past.weekday() == 6:
        past -= timedelta(days=1)
    p = _propose(client, shop, job, [_op(shop["rina"], start=_iso(past, 9))],
                 pinSequence=1, lockBeforeSequence=1, anchor=_at(past, 8))
    assert [x["code"] for x in p["problems"]] == ["PAST_START"]

    db.session.add(WorkCalendarException(date=MON, type=CalendarExceptionType.HOLIDAY_NO_WORK))
    db.session.commit()
    p = _propose(client, shop, job, [_op(shop["rina"], start=_iso(MON, 9))],
                 pinSequence=1, lockBeforeSequence=1)
    op = p["operations"][0]
    assert _parse(op["scheduledStart"]) == _at(TUE, 8)
    assert "next working time" in op["message"]
    assert p["problems"] == []


def test_replanning_after_a_redo_places_it_without_clashing(client, shop):
    other = _job(shop, status=JobOrderStatus.SCHEDULED)
    _booked(other, 1, shop["rina"], _at(MON, 8), _at(MON, 12))

    job = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    done = _booked(job, 1, shop["rina"], _at(LAST_SAT, 8), _at(LAST_SAT, 10),
                   status=OperationStatus.COMPLETED, actual_start=_at(LAST_SAT, 8))
    done.actual_end = _at(LAST_SAT, 10)
    _booked(job, 2, shop["omar"], _at(MON, 14), _at(MON, 16))
    db.session.commit()
    res = client.post(
        f"/api/v1/operations/{done.id}/rework",
        json={"category": "OPERATOR_ERROR"},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 201, res.get_json()

    p = _propose(client, shop, db.session.get(JobOrder, job.id))
    by_seq = {o["sequenceNo"]: o for o in p["operations"]}
    assert _parse(by_seq[2]["scheduledStart"]) == _at(MON, 13)
    assert _parse(by_seq[3]["scheduledStart"]) == _at(MON, 15)
    assert p["problems"] == []
    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/apply",
        json={"operations": [
            {"id": o["id"], "scheduledStart": o["scheduledStart"], "machineUnitId": o["machineUnitId"]}
            for o in p["operations"] if o["sequenceNo"] > 1
        ]},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    assert _double_bookings() == []


# ---- Load --------------------------------------------------------------------


def test_twenty_jobs_proposed_and_confirmed_without_double_booking(client, shop):
    leads = [shop["rina"], shop["omar"]]
    timings = []
    for n in range(20):
        job = _job(shop)
        ops = [
            _op(leads[n % 2], 1, 3, machine=shop["laser"]),
            _op(shop["gio"] if n % 3 else shop["ben"], 2, 2, machine=shop["lathe"],
                helpers=[shop["pia"]] if n % 4 == 0 else []),
            _op(None, 3, type_=shop["heat"]) if n % 5 == 0 else _op(shop["omar"], 3, 1, machine=shop["shaper"]),
        ]
        began = clock.perf_counter()
        p = _propose(client, shop, job, ops)
        timings.append(clock.perf_counter() - began)
        assert all(o["scheduled"] for o in p["operations"]), p["operations"]
        assert p["problems"] == [], p["problems"]
        res = _confirm(client, shop, job, ops, p)
        assert res.status_code == 200, res.get_json()
    assert _double_bookings() == []
    assert max(timings) < 2.0, timings


# ---- Redo is scheduled right away --------------------------------------------


def _rework(client, shop, op):
    res = client.post(
        f"/api/v1/operations/{op.id}/rework",
        json={"category": "OPERATOR_ERROR"},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 201, res.get_json()
    db.session.expire_all()
    return res.get_json()


def _completed(job, lead, unit=None):
    done = _booked(job, 1, lead, _at(LAST_SAT, 8), _at(LAST_SAT, 10), unit=unit,
                   status=OperationStatus.COMPLETED, actual_start=_at(LAST_SAT, 8))
    done.actual_end = _at(LAST_SAT, 10)
    db.session.commit()
    return done


def test_redo_is_placed_right_after_its_original_and_pushes_the_next_operation(client, shop):
    job = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    done = _completed(job, shop["rina"])
    redo_start, _end, _ = place_from_start(shop["rina"].id, _next_quarter_hour(), 2)
    next_start, next_end, _ = place_from_start(shop["omar"].id, redo_start, 2)
    _booked(job, 2, shop["omar"], next_start, next_end)
    later = MON + timedelta(days=7)
    _booked(job, 3, shop["ben"], _at(later, 8), _at(later, 10))
    _rework(client, shop, done)

    job = db.session.get(JobOrder, job.id)
    ops = {op.sequence_no: op for op in job.operations}
    redo = ops[2]
    assert redo.rework_of_operation_id == done.id
    assert redo.assigned_worker_id == shop["rina"].id
    assert redo_start <= ensure_utc(redo.scheduled_start) <= redo_start + timedelta(minutes=15)
    assert ops[3].assigned_worker_id == shop["omar"].id
    assert ensure_utc(ops[3].scheduled_start) >= ensure_utc(redo.scheduled_end)
    assert ensure_utc(ops[3].scheduled_start) > next_start
    assert ensure_utc(ops[4].scheduled_start) == _at(later, 8)
    assert ensure_utc(ops[1].actual_start) == _at(LAST_SAT, 8)
    assert job.to_dict()["needsReplan"] is False
    assert _double_bookings() == []


def test_redo_that_cannot_be_placed_marks_the_job_needs_replan(client, shop):
    job = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    done = _completed(job, shop["rina"], unit=shop["laser1"])
    _booked(job, 2, shop["omar"], _at(MON, 8), _at(MON, 10))
    db.session.add(MachineDowntime(
        machine_unit_id=shop["laser1"].id, started_at=_at(LAST_SAT, 11),
        category=DowntimeCategory.MECHANICAL_FAILURE, reason="Lens cracked",
        reported_by_id=shop["admin"].id,
    ))
    db.session.commit()
    _rework(client, shop, done)

    job = db.session.get(JobOrder, job.id)
    redo = next(op for op in job.operations if op.rework_of_operation_id == done.id)
    assert redo.scheduled_start is None
    nxt = next(op for op in job.operations if op.assigned_worker_id == shop["omar"].id)
    assert ensure_utc(nxt.scheduled_start) == _at(MON, 8)

    headers = _headers(shop["admin"])
    page = client.get(f"/api/v1/job-orders/{job.id}", headers=headers).get_json()
    assert page["needsReplan"] is True
    assert "Laser #1 is down with no expected repair date" in page["needsReplanReason"]
    listed = {j["id"]: j for j in client.get("/api/v1/job-orders", headers=headers).get_json()}
    assert listed[job.id]["needsReplan"] is True
    alerts = StaffAlert.query.filter_by(kind=StaffAlertKind.NEEDS_REPLAN, job_order_id=job.id).all()
    assert [a.recipient_id for a in alerts] == [shop["admin"].id]

    # Once the laser is back, the Admin re-plans and the mark goes away.
    MachineDowntime.query.update({MachineDowntime.ended_at: datetime.now(timezone.utc)})
    db.session.commit()
    p = _propose(client, shop, job, anchor=_next_quarter_hour())
    assert p["problems"] == []
    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/apply",
        json={"operations": [
            {"id": o["id"], "scheduledStart": o["scheduledStart"], "machineUnitId": o["machineUnitId"]}
            for o in p["operations"] if o["sequenceNo"] > 1
        ]},
        headers=headers,
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["needsReplan"] is False


# ---- Overrunning work keeps its machine and crew ------------------------------


def test_overrunning_operation_blocks_its_unit_and_crew_until_the_work_can_finish(client, shop):
    now = datetime.now(timezone.utc)
    busy = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    _booked(busy, 1, shop["ben"], now - timedelta(days=3), now - timedelta(days=2),
            unit=shop["shaper1"], hours=60, status=OperationStatus.IN_PROGRESS,
            actual_start=now - timedelta(days=3))
    # Nothing logged yet, so all 60 target hours are still to do.
    _s, until, _ = place_from_start(shop["ben"].id, now, 60)
    anchor = _next_quarter_hour()

    p = _propose(client, shop, _job(shop), [_op(shop["omar"], machine=shop["shaper"])], anchor=anchor)
    assert _starts(p)[0] >= until - timedelta(minutes=1)
    p = _propose(client, shop, _job(shop), [_op(shop["ben"])], anchor=anchor)
    assert _starts(p)[0] >= until - timedelta(minutes=1)


def test_overrun_past_its_target_still_blocks_at_least_one_more_working_hour(client, shop):
    from app.models.operation_time import OperationTimeEvent, OperationTimeLog

    now = datetime.now(timezone.utc)
    busy = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    op = _booked(busy, 1, shop["ben"], now - timedelta(days=3), now - timedelta(days=2),
                 unit=shop["shaper1"], hours=1, status=OperationStatus.IN_PROGRESS,
                 actual_start=now - timedelta(days=3))
    db.session.add(OperationTimeLog(
        operation_id=op.id, worker_id=shop["ben"].id,
        event=OperationTimeEvent.START, event_at=now - timedelta(days=3),
    ))
    db.session.commit()
    _s, until, _ = place_from_start(shop["ben"].id, now, 1)

    p = _propose(client, shop, _job(shop), [_op(shop["omar"], machine=shop["shaper"])],
                 anchor=_next_quarter_hour())
    assert _starts(p)[0] >= until - timedelta(minutes=1)


# ---- Every machine operation has a unit ---------------------------------------


def test_typed_start_on_a_machine_operation_picks_a_free_unit(client, shop):
    other = _job(shop, status=JobOrderStatus.SCHEDULED)
    _booked(other, 1, shop["ben"], _at(MON, 8), _at(MON, 12), unit=shop["lathe8"])
    job = _job(shop)
    ops = [_op(shop["gio"], hours=2, machine=shop["lathe"], start=_iso(MON, 9))]
    p = _propose(client, shop, job, ops, pinSequence=1, lockBeforeSequence=1)
    op = p["operations"][0]
    assert (op["machineUnitId"], op["machineUnitLabel"]) == (shop["lathe2"].id, "Lathe #2")
    assert p["problems"] == []
    assert _confirm(client, shop, job, ops, p).status_code == 200
    assert _double_bookings() == []


def test_confirm_refuses_a_machine_operation_without_a_unit(client, shop):
    other = _job(shop, status=JobOrderStatus.SCHEDULED)
    _booked(other, 1, shop["rina"], _at(MON, 8), _at(MON, 12), unit=shop["laser1"])
    job = _job(shop)
    ops = [_op(shop["omar"], hours=2, machine=shop["laser"], start=_iso(MON, 9))]
    p = _propose(client, shop, job, ops, pinSequence=1, lockBeforeSequence=1)
    assert p["operations"][0]["machineUnitId"] is None
    assert "NO_MACHINE_UNIT" in [x["code"] for x in p["problems"]]
    _refused(_confirm(client, shop, job, ops, p), "#1 Step 1 needs a machine unit")

    free_time = [_op(shop["omar"], hours=2, machine=shop["laser"], start=_iso(TUE, 9))]
    _refused(_confirm(client, shop, job, free_time), "#1 Step 1 needs a machine unit")
    assert db.session.get(JobOrder, job.id).status == JobOrderStatus.DRAFT
    assert _double_bookings() == []


# ---- People with no working hours ---------------------------------------------

NO_HOURS = "Eli has no working hours set; set them on Worker setup"
DEFAULT_HOURS = [(d, True, time(8, 0), time(17, 0)) for d in range(6)] + [(6, False, None, None)]


def _hours(user_id):
    rows = WorkerSchedule.query.filter_by(worker_id=user_id).order_by(WorkerSchedule.day_of_week)
    return [(r.day_of_week, r.is_working, r.start_time, r.end_time) for r in rows]


def test_worker_with_no_hours_is_unavailable_alone_or_as_a_helper(client, shop):
    eli = _user("sc_eli@test.local", UserRole.PRODUCTION_WORKER, "Eli")
    WorkerSchedule.query.filter_by(worker_id=eli.id).delete()
    db.session.commit()

    for ops in ([_op(eli, hours=2)], [_op(shop["rina"], hours=2, helpers=[eli])]):
        job = _job(shop)
        op = _propose(client, shop, job, ops)["operations"][0]
        assert op["scheduled"] is False
        assert NO_HOURS in op["message"]
        res = _confirm(client, shop, job, [{**ops[0], "scheduledStart": _iso(MON, 9)}])
        assert res.status_code in (400, 409), res.get_json()
        assert NO_HOURS in res.get_json()["error"]["message"]
    assert _double_bookings() == []


def test_every_new_worker_profile_gets_the_shop_default_hours(app, shop):
    from app.models.user_security import InvitationChannel
    from app.services.invitation_service import create_invited_user

    # Admins are not assigned work, so they get no worker profile or hours.
    admin2 = _user("sc_admin2@test.local", UserRole.ADMIN, "Second Admin")
    ensure_worker_profile(admin2)
    db.session.commit()
    assert _hours(admin2.id) == []

    office = shop["office"]
    update_user(office, {"role": UserRole.PRODUCTION_WORKER})
    assert _hours(office.id) == DEFAULT_HOURS

    invited, _inv, _secret = create_invited_user(
        full_name="Invited Worker", email="sc_invited@test.local", mobile_number="09171234567",
        role=UserRole.PRODUCTION_WORKER, channel=InvitationChannel.EMAIL, created_by_id=shop["admin"].id,
    )
    assert _hours(invited.id) == DEFAULT_HOURS


# ---- Confirms run one at a time -----------------------------------------------


def test_two_confirms_at_the_same_moment_only_one_succeeds(app, client, shop, monkeypatch):
    a, b = _job(shop), _job(shop)
    ops = [_op(shop["rina"], hours=2)]
    bodies = {job.id: _merged(ops, _propose(client, shop, job, ops)) for job in (a, b)}
    headers = _headers(shop["admin"])

    # Each confirm waits after its clash check until the other has checked too,
    # so without a lock both would pass before either is saved.
    both_checked = threading.Barrier(2)
    check = job_order_service._assert_schedule_has_no_problems

    def check_then_wait(job, lead):
        check(job, lead)
        try:
            both_checked.wait(timeout=3)
        except threading.BrokenBarrierError:
            pass

    monkeypatch.setattr(job_order_service, "_assert_schedule_has_no_problems", check_then_wait)
    codes = []

    def confirm(job_id, body):
        res = app.test_client().post(
            f"/api/v1/job-orders/{job_id}/schedule/confirm", json={"operations": body}, headers=headers
        )
        codes.append(res.status_code)

    threads = [threading.Thread(target=confirm, args=item) for item in bodies.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(codes) == [200, 409]
    db.session.expire_all()
    assert _double_bookings() == []


# ---- Only confirmed jobs reserve time -----------------------------------------


def test_a_pending_jobs_saved_times_do_not_block_other_jobs(client, shop):
    draft = _job(shop)
    _booked(draft, 1, shop["rina"], _at(MON, 8), _at(MON, 10), unit=shop["laser1"],
            status=OperationStatus.PENDING)
    assigned_draft = _job(shop)
    _booked(assigned_draft, 1, shop["omar"], _at(MON, 8), _at(MON, 10))

    job = _job(shop)
    ops = [_op(shop["rina"], hours=2, machine=shop["laser"]), _op(shop["omar"], 2, 2)]
    p = _propose(client, shop, job, ops)
    assert _starts(p) == [_at(MON, 8), _at(MON, 10)]
    assert _starts(_propose(client, shop, job, [_op(shop["omar"], hours=2)])) == [_at(MON, 8)]
    assert p["problems"] == []
    assert _confirm(client, shop, job, ops, p).status_code == 200

    # The pending job now has to fit around the confirmed one.
    assert _starts(_propose(client, shop, draft)) == [_at(MON, 10)]
