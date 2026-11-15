"""Schedule step rules: End follows Start and target hours across working time,
moving a Start re-places the following operations, blocking checks refuse
confirmation, and a saved schedule that starts in the past is replaced.

Uses the bmsc_test database from conftest (schema built from the models).
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.machine import MachineType, MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.models.user import User, UserRole, UserStatus
from app.models.worker_profile import WorkerProfile
from app.models.worker_skill import (
    CalendarExceptionType,
    WorkCalendarException,
    WorkerSchedule,
    WorkerSkill,
)
from app.services.schedule_calendar import shop_local_to_utc, shop_now
from app.services.schedule_service import place_from_start

_today = shop_now().date()
# A Monday one to two weeks out: inside the proposal horizon, never in the past.
MON = _today + timedelta(days=(7 - _today.weekday()) % 7 + 7)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
SAT = MON + timedelta(days=5)
NEXT_MON = MON + timedelta(days=7)


def _at(d, hh, mm=0):
    return shop_local_to_utc(d, time(hh, mm))


def _iso(d, hh, mm=0):
    return _at(d, hh, mm).isoformat()


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _user(email, role, name, hours=(time(8, 0), time(17, 0))):
    user = User(
        email=email,
        password_hash="x",
        full_name=name,
        role=role,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    if role == UserRole.PRODUCTION_WORKER:
        db.session.add(WorkerProfile(user_id=user.id))
        for dow in range(6):
            db.session.add(
                WorkerSchedule(
                    worker_id=user.id,
                    day_of_week=dow,
                    is_working=True,
                    start_time=hours[0],
                    end_time=hours[1],
                )
            )
    return user


def _headers(user):
    token = create_access_token(identity=user.id, additional_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def shop(app):
    admin = _user("rules_admin@test.local", UserRole.ADMIN, "Rules Admin")
    worker = _user("rules_worker@test.local", UserRole.PRODUCTION_WORKER, "Rina")
    other = _user("rules_other@test.local", UserRole.PRODUCTION_WORKER, "Omar")
    night = _user(
        "rules_night@test.local",
        UserRole.PRODUCTION_WORKER,
        "Nina",
        hours=(time(18, 0), time(23, 0)),
    )
    mtype = MachineType(code="MILL_R", name="Mill R", units=1)
    db.session.add(mtype)
    db.session.flush()
    unit = MachineUnit(machine_type_id=mtype.id, label="Mill R-1", active=True)
    client_row = Client(name="Rules Client")
    db.session.add_all([unit, client_row])
    for person in (worker, other, night):
        db.session.add(WorkerSkill(worker_id=person.id, machine_type_id=mtype.id, proficiency=4))
    db.session.commit()
    return {
        "admin": admin,
        "worker": worker,
        "other": other,
        "night": night,
        "unit": unit,
        "client": client_row,
    }


def _job(shop, status=JobOrderStatus.DRAFT):
    job = JobOrder(
        client_id=shop["client"].id,
        title="Rules Job",
        due_date=MON + timedelta(days=30),
        status=status,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        material_status=MaterialStatus.NOT_REQUIRED,
        created_by_id=shop["admin"].id,
    )
    db.session.add(job)
    db.session.commit()
    return job


def _op(job, seq, worker, start=None, end=None, hours=2, unit=None, status=None):
    op = JobOperation(
        job_order_id=job.id,
        sequence_no=seq,
        operation_name=f"Step {seq}",
        assigned_worker_id=worker.id,
        estimated_hours=Decimal(str(hours)),
        machine_unit_id=unit.id if unit else None,
        scheduled_start=start,
        scheduled_end=end,
        status=status
        or (OperationStatus.PENDING if job.status == JobOrderStatus.DRAFT else OperationStatus.SCHEDULED),
    )
    db.session.add(op)
    db.session.flush()
    return op


def _payload(worker, start, seq=1, hours=2, unit=None):
    return {
        "sequenceNo": seq,
        "operationName": f"Step {seq}",
        "assignedWorkerId": worker.id,
        "estimatedHours": hours,
        "scheduledStart": start,
        "machineTypeId": unit.machine_type_id if unit else None,
        "machineUnitId": unit.id if unit else None,
    }


def _confirm(client, shop, job, operations):
    return client.post(
        f"/api/v1/job-orders/{job.id}/schedule/confirm",
        json={"operations": operations},
        headers=_headers(shop["admin"]),
    )


def _propose(client, shop, job, body):
    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/propose",
        json=body,
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    return res.get_json()


# ---- End follows Start and target hours ------------------------------------


def test_eight_hours_from_1600_end_next_working_day_not_midnight(shop):
    start, end, segments = place_from_start(shop["worker"].id, _at(MON, 16), 8)
    assert start == _at(MON, 16)
    # Mon 16-17, Tue 08-12, the break, Tue 13-16.
    assert end == _at(TUE, 16)
    assert segments == [
        (_at(MON, 16), _at(MON, 17)),
        (_at(TUE, 8), _at(TUE, 12)),
        (_at(TUE, 13), _at(TUE, 16)),
    ]


def test_end_skips_sundays_and_holidays_and_uses_overtime(shop):
    _start, end, _ = place_from_start(shop["worker"].id, _at(SAT, 16), 8)
    assert end == _at(NEXT_MON, 16)

    db.session.add(WorkCalendarException(date=TUE, type=CalendarExceptionType.HOLIDAY_NO_WORK))
    db.session.add(
        WorkCalendarException(
            date=WED, type=CalendarExceptionType.OVERTIME, start_time=time(17, 0), end_time=time(19, 0)
        )
    )
    db.session.flush()
    _start, end, _ = place_from_start(shop["worker"].id, _at(MON, 16), 11)
    # Mon 16-17 (1h), Tue holiday, Wed 08-19 with overtime less the break (10h).
    assert end == _at(WED, 19)


def test_start_edit_in_proposal_ends_after_target_hours(client, shop):
    job = _job(shop)
    body = {
        "operations": [_payload(shop["worker"], _iso(MON, 16), hours=8)],
        "pinSequence": 1,
        "lockBeforeSequence": 1,
    }
    op = _propose(client, shop, job, body)["operations"][0]
    assert _parse(op["scheduledStart"]) == _at(MON, 16)
    assert _parse(op["scheduledEnd"]) == _at(TUE, 16)


def test_start_typed_outside_working_hours_moves_to_next_working_time(client, shop):
    job = _job(shop)
    body = {
        "operations": [_payload(shop["worker"], _iso(MON, 19))],
        "pinSequence": 1,
        "lockBeforeSequence": 1,
    }
    op = _propose(client, shop, job, body)["operations"][0]
    assert _parse(op["scheduledStart"]) == _at(TUE, 8)
    assert _parse(op["scheduledEnd"]) == _at(TUE, 10)
    assert "next working time" in op["message"]


def test_confirm_ignores_a_typed_end(client, shop):
    job = _job(shop)
    payload = _payload(shop["worker"], _iso(MON, 16), hours=8)
    payload["scheduledEnd"] = _iso(TUE, 0)
    res = _confirm(client, shop, job, [payload])
    assert res.status_code == 200, res.get_json()
    assert _parse(res.get_json()["operations"][0]["scheduledEnd"]) == _at(TUE, 16)


# ---- Moving a Start re-places the following operations ---------------------


def test_moving_start_later_pushes_next_operation(client, shop):
    job = _job(shop)
    ops = [
        _payload(shop["worker"], _iso(MON, 14), seq=1, hours=2),
        _payload(shop["other"], _iso(MON, 10), seq=2, hours=2),
    ]
    result = _propose(client, shop, job, {"operations": ops, "pinSequence": 1, "lockBeforeSequence": 1})
    first, second = result["operations"]
    assert _parse(first["scheduledEnd"]) == _at(MON, 16)
    assert _parse(second["scheduledStart"]) == _at(MON, 16)
    assert second["assignedWorkerId"] == shop["other"].id
    assert result["problems"] == []


def test_pending_job_following_operation_moves_earlier_too(client, shop):
    job = _job(shop)
    ops = [
        _payload(shop["worker"], _iso(MON, 8), seq=1, hours=2),
        _payload(shop["other"], _iso(WED, 8), seq=2, hours=2),
    ]
    result = _propose(client, shop, job, {"operations": ops, "pinSequence": 1, "lockBeforeSequence": 1})
    assert _parse(result["operations"][1]["scheduledStart"]) == _at(MON, 10)


def test_released_job_following_operation_never_moves_earlier(client, shop):
    job = _job(shop, status=JobOrderStatus.SCHEDULED)
    _op(job, 1, shop["worker"], _at(MON, 8), _at(MON, 10))
    _op(job, 2, shop["other"], _at(WED, 8), _at(WED, 10))
    db.session.commit()
    result = _propose(client, shop, job, {})
    assert _parse(result["operations"][1]["scheduledStart"]) == _at(WED, 8)


def test_following_operation_keeps_its_machine_unit_and_avoids_other_jobs(client, shop):
    busy = _job(shop, status=JobOrderStatus.SCHEDULED)
    _op(busy, 1, shop["night"], _at(MON, 18), _at(MON, 20), unit=shop["unit"])
    _op(busy, 2, shop["other"], _at(TUE, 8), _at(TUE, 12), hours=4, unit=shop["unit"])
    db.session.commit()
    job = _job(shop)
    ops = [
        _payload(shop["worker"], _iso(MON, 15), seq=1, hours=2),
        _payload(shop["worker"], None, seq=2, hours=2, unit=shop["unit"]),
    ]
    result = _propose(
        client,
        shop,
        job,
        {"operations": ops, "pinSequence": 1, "lockBeforeSequence": 1, "honorMachinePins": True},
    )
    second = result["operations"][1]
    assert second["machineUnitId"] == shop["unit"].id
    # The unit is booked until 12:00, then the break.
    assert _parse(second["scheduledStart"]) == _at(TUE, 13)


# ---- Blocking checks refuse confirmation -----------------------------------


def _refused(res, code_text):
    assert res.status_code == 409, res.get_json()
    err = res.get_json()["error"]
    assert err["code"] == "SCHEDULE_INVALID"
    assert code_text in err["message"], err["message"]
    return err["message"]


def test_confirm_refuses_operation_starting_before_previous_ends(client, shop):
    job = _job(shop)
    res = _confirm(
        client,
        shop,
        job,
        [
            _payload(shop["worker"], _iso(MON, 8), seq=1, hours=4),
            _payload(shop["other"], _iso(MON, 10), seq=2),
        ],
    )
    _refused(res, "#2 Step 2 starts before #1 Step 1 ends")
    assert db.session.get(JobOrder, job.id).status == JobOrderStatus.DRAFT


def test_confirm_refuses_worker_booked_twice_in_job(client, shop):
    job = _job(shop)
    res = _confirm(
        client,
        shop,
        job,
        [
            _payload(shop["worker"], _iso(MON, 8), seq=1, hours=4),
            _payload(shop["worker"], _iso(MON, 9), seq=2),
        ],
    )
    _refused(res, "Rina is booked on #1 Step 1")


def test_confirm_refuses_worker_booked_on_another_job(client, shop):
    busy = _job(shop, status=JobOrderStatus.SCHEDULED)
    _op(busy, 1, shop["worker"], _at(MON, 8), _at(MON, 12), hours=4)
    db.session.commit()
    job = _job(shop)
    res = _confirm(client, shop, job, [_payload(shop["worker"], _iso(MON, 10))])
    _refused(res, f"Rina is already booked on {busy.job_number}")


def test_confirm_refuses_machine_booked_twice_in_job(client, shop):
    job = _job(shop)
    res = _confirm(
        client,
        shop,
        job,
        [
            _payload(shop["worker"], _iso(MON, 8), seq=1, hours=4, unit=shop["unit"]),
            _payload(shop["other"], _iso(MON, 9), seq=2, unit=shop["unit"]),
        ],
    )
    _refused(res, "Mill R-1 is booked on #1 Step 1")


def test_confirm_refuses_machine_booked_on_another_job(client, shop):
    busy = _job(shop, status=JobOrderStatus.SCHEDULED)
    _op(busy, 1, shop["other"], _at(MON, 8), _at(MON, 12), hours=4, unit=shop["unit"])
    db.session.commit()
    job = _job(shop)
    res = _confirm(client, shop, job, [_payload(shop["worker"], _iso(MON, 10), unit=shop["unit"])])
    _refused(res, f"Mill R-1 is already booked on {busy.job_number}")


def test_clashes_compare_working_periods_not_the_whole_span(client, shop):
    # Omar's overnight operation spans Mon 16:00 to Tue 09:00 but only works
    # Mon 16-17 and Tue 08-09; Nina's evening shift on the same unit is free.
    busy = _job(shop, status=JobOrderStatus.SCHEDULED)
    _op(busy, 1, shop["other"], _at(MON, 16), _at(TUE, 9), hours=2, unit=shop["unit"])
    db.session.commit()
    job = _job(shop)
    res = _confirm(client, shop, job, [_payload(shop["night"], _iso(MON, 18), unit=shop["unit"])])
    assert res.status_code == 200, res.get_json()


def test_confirm_refuses_work_outside_working_hours(client, shop):
    job = _job(shop)
    res = _confirm(client, shop, job, [_payload(shop["worker"], _iso(MON, 18))])
    _refused(res, "runs outside working hours")


def test_confirm_refuses_work_on_a_holiday(client, shop):
    db.session.add(WorkCalendarException(date=MON, type=CalendarExceptionType.HOLIDAY_NO_WORK))
    db.session.commit()
    job = _job(shop)
    res = _confirm(client, shop, job, [_payload(shop["worker"], _iso(MON, 9))])
    _refused(res, "on a holiday")


def test_confirm_refuses_start_in_the_past(client, shop):
    past = (datetime.now(timezone.utc) - timedelta(days=14)).date()
    while past.weekday() == 6:
        past -= timedelta(days=1)
    job = _job(shop)
    res = _confirm(client, shop, job, [_payload(shop["worker"], _iso(past, 9))])
    _refused(res, "starts in the past")


def test_released_replan_checks_every_operation_not_only_moved_ones(client, shop):
    job = _job(shop, status=JobOrderStatus.SCHEDULED)
    _op(job, 1, shop["worker"], _at(MON, 8), _at(MON, 12), hours=4)
    second = _op(job, 2, shop["other"], _at(TUE, 8), _at(TUE, 10))
    db.session.commit()
    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/apply",
        json={"operations": [{"id": second.id, "scheduledStart": _iso(MON, 9)}]},
        headers=_headers(shop["admin"]),
    )
    _refused(res, "#2 Step 2 starts before #1 Step 1 ends")


def test_problems_stay_in_every_proposal(client, shop):
    busy = _job(shop, status=JobOrderStatus.SCHEDULED)
    _op(busy, 1, shop["worker"], _at(MON, 8), _at(MON, 12), hours=4)
    db.session.commit()
    job = _job(shop)
    body = {
        "operations": [_payload(shop["worker"], _iso(MON, 10))],
        "pinSequence": 1,
        "lockBeforeSequence": 1,
    }
    result = _propose(client, shop, job, body)
    assert _parse(result["operations"][0]["scheduledStart"]) == _at(MON, 10)
    assert [p["code"] for p in result["problems"]] == ["WORKER_CONFLICT"]
    assert result["problems"][0]["sequenceNo"] == 1


# ---- Opening the Schedule step ---------------------------------------------


def test_saved_schedule_in_the_past_is_replaced_on_opening(client, shop):
    job = _job(shop)
    _op(job, 1, shop["worker"], _at(date(2026, 9, 19), 8), _at(date(2026, 9, 19), 10))
    db.session.commit()
    result = _propose(client, shop, job, {"restoreSaved": True})
    assert result["restored"] is False
    assert _parse(result["replacedPastStart"]) == _at(date(2026, 9, 19), 8)
    assert _parse(result["operations"][0]["scheduledStart"]) >= datetime.now(timezone.utc)
    assert result["problems"] == []


def test_saved_future_schedule_is_shown_on_opening(client, shop):
    job = _job(shop)
    _op(job, 1, shop["worker"], _at(WED, 9), _at(WED, 11))
    db.session.commit()
    result = _propose(client, shop, job, {"restoreSaved": True})
    assert result["restored"] is True
    assert result["replacedPastStart"] is None
    assert _parse(result["operations"][0]["scheduledStart"]) == _at(WED, 9)
