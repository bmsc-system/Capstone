"""Saved units are kept, faster units are only suggested, other operators'
units give a warning (never a block), and same-day jobs keep a stable order."""

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
from app.models.worker_skill import WorkerSchedule, WorkerSkill
from app.services.overdue_replan_service import replan_overdue
from app.services.schedule_calendar import shop_local_to_utc, shop_now

_today = shop_now().date()
MON = _today + timedelta(days=(7 - _today.weekday()) % 7 + 7)


def _at(d, hh, mm=0):
    return shop_local_to_utc(d, time(hh, mm))


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _user(email, role, name):
    user = User(email=email, password_hash="x", full_name=name, role=role,
                status=UserStatus.ACTIVE, active=True)
    db.session.add(user)
    db.session.flush()
    if role == UserRole.PRODUCTION_WORKER:
        db.session.add(WorkerProfile(user_id=user.id))
        for dow in range(6):
            db.session.add(WorkerSchedule(worker_id=user.id, day_of_week=dow, is_working=True,
                                          start_time=time(8, 0), end_time=time(17, 0)))
    return user


def _headers(user):
    token = create_access_token(identity=user.id, additional_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def shop(app):
    admin = _user("pu_admin@test.local", UserRole.ADMIN, "Pu Admin")
    gio = _user("pu_gio@test.local", UserRole.PRODUCTION_WORKER, "Gio Reyes")
    ben = _user("pu_ben@test.local", UserRole.PRODUCTION_WORKER, "Ben Cruz")
    ana = _user("pu_ana@test.local", UserRole.PRODUCTION_WORKER, "Ana Lim")
    lathe = MachineType(code="LATHE_PU", name="Lathe", units=2)
    db.session.add(lathe)
    db.session.flush()
    gios = MachineUnit(machine_type_id=lathe.id, label="Lathe #2", active=True, default_operator_id=gio.id)
    open_unit = MachineUnit(machine_type_id=lathe.id, label="Lathe #8", active=True)
    client_row = Client(name="Pu Client")
    db.session.add_all([gios, open_unit, client_row])
    for person in (gio, ben, ana):
        db.session.add(WorkerSkill(worker_id=person.id, machine_type_id=lathe.id, proficiency=3))
    db.session.commit()
    return {"admin": admin, "gio": gio, "ben": ben, "ana": ana, "lathe": lathe,
            "gios": gios, "open": open_unit, "client": client_row}


def _job(shop, status=JobOrderStatus.DRAFT, **extra):
    job = JobOrder(client_id=shop["client"].id, title="Pu Job", due_date=MON + timedelta(days=30),
                   status=status, job_type=JobType.FABRICATION,
                   part_condition=PartCondition.RAW_MATERIAL,
                   material_status=MaterialStatus.NOT_REQUIRED, created_by_id=shop["admin"].id, **extra)
    db.session.add(job)
    db.session.commit()
    return job


def _booked(shop, lead, unit, start, end, seq=1):
    job = _job(shop, JobOrderStatus.SCHEDULED)
    op = JobOperation(job_order_id=job.id, sequence_no=seq, operation_name="Booked",
                      assigned_worker_id=lead.id,
                      estimated_hours=Decimal(str((end - start).total_seconds() / 3600)),
                      machine_type_id=unit.machine_type_id, machine_unit_id=unit.id,
                      scheduled_start=start, scheduled_end=end, status=OperationStatus.SCHEDULED)
    db.session.add(op)
    db.session.commit()
    return op


def _op(shop, lead, unit=None, hours=2):
    return {"sequenceNo": 1, "operationName": "Facing", "assignedWorkerId": lead.id,
            "estimatedHours": hours, "machineTypeId": shop["lathe"].id,
            "machineUnitId": unit.id if unit else None}


def _propose(client, shop, job, ops, **extra):
    body = {"anchor": _at(MON, 8).isoformat(), "operations": ops, **extra}
    res = client.post(f"/api/v1/job-orders/{job.id}/schedule/propose", json=body,
                      headers=_headers(shop["admin"]))
    assert res.status_code == 200, res.get_json()
    return res.get_json()


# --- 1. The saved unit is kept ---------------------------------------------

def test_first_proposal_keeps_the_saved_unit_even_when_another_is_free_earlier(client, shop):
    _booked(shop, shop["ben"], shop["gios"], _at(MON, 8), _at(MON, 15))
    job = _job(shop)
    result = _propose(client, shop, job, [_op(shop, shop["gio"], shop["gios"])])
    op = result["operations"][0]
    assert op["machineUnitId"] == shop["gios"].id
    assert _parse(op["scheduledStart"]) == _at(MON, 15)


def test_the_unit_is_only_chosen_freely_when_asked_explicitly(client, shop):
    _booked(shop, shop["ben"], shop["gios"], _at(MON, 8), _at(MON, 15))
    job = _job(shop)
    result = _propose(client, shop, job, [_op(shop, shop["gio"], shop["gios"])], honorMachinePins=False)
    assert result["operations"][0]["machineUnitId"] == shop["open"].id


def test_an_automatic_re_plan_never_swaps_the_saved_unit(app, shop):
    job = _job(shop, JobOrderStatus.SCHEDULED)
    past = shop_now().date() - timedelta(days=2)
    db.session.add(JobOperation(job_order_id=job.id, sequence_no=1, operation_name="Facing",
                                assigned_worker_id=shop["gio"].id, estimated_hours=Decimal("2"),
                                machine_type_id=shop["lathe"].id, machine_unit_id=shop["gios"].id,
                                scheduled_start=_at(past, 8), scheduled_end=_at(past, 10),
                                status=OperationStatus.SCHEDULED))
    db.session.commit()
    # Gio's unit is booked from shortly after now until tomorrow evening; the open unit is free.
    soon = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=5)
    _booked(shop, shop["ben"], shop["gios"], soon, _at(shop_now().date() + timedelta(days=1), 17))
    result = replan_overdue(apply=True)
    assert [r["jobNumber"] for r in result["moved"]] == [job.job_number]
    db.session.expire_all()
    assert db.session.get(JobOrder, job.id).operations[0].machine_unit_id == shop["gios"].id


# --- 2/4. Ownership: rank own → open → others, warn but never block ---------

def test_a_lead_without_a_unit_gets_an_open_unit_before_another_operators(client, shop):
    job = _job(shop)
    result = _propose(client, shop, job, [_op(shop, shop["ben"])], honorMachinePins=False)
    assert result["operations"][0]["machineUnitId"] == shop["open"].id


def test_a_lead_gets_their_own_unit_when_both_are_free(client, shop):
    job = _job(shop)
    result = _propose(client, shop, job, [_op(shop, shop["gio"])], honorMachinePins=False)
    assert result["operations"][0]["machineUnitId"] == shop["gios"].id


def test_another_operators_unit_is_a_warning_not_a_blocker(client, shop):
    job = _job(shop)
    ops = [_op(shop, shop["ben"], shop["gios"])]
    result = _propose(client, shop, job, ops)
    assert result["problems"] == []
    assert [w["code"] for w in result["warnings"]] == ["UNIT_OWNER"]
    assert result["warnings"][0]["severity"] == "warning"
    assert "Ben Cruz is placed on Lathe #2, Gio Reyes's unit" in result["warnings"][0]["message"]
    confirmed = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/confirm",
        json={"operations": [{**ops[0], "scheduledStart": result["operations"][0]["scheduledStart"]}]},
        headers=_headers(shop["admin"]),
    )
    assert confirmed.status_code == 200, confirmed.get_json()


def test_own_or_open_units_give_no_warning(client, shop):
    job = _job(shop)
    for lead, unit in ((shop["gio"], shop["gios"]), (shop["ben"], shop["open"])):
        result = _propose(client, shop, job, [_op(shop, lead, unit)])
        assert result["warnings"] == []


def test_the_typed_start_unit_chooser_notes_another_operators_unit(client, shop):
    # The open unit is busy at the typed start, so only Gio's unit is free.
    _booked(shop, shop["ana"], shop["open"], _at(MON, 8), _at(MON, 17))
    job = _job(shop)
    op = {**_op(shop, shop["ben"]), "scheduledStart": _at(MON, 9).isoformat()}
    result = _propose(client, shop, job, [op], pinSequence=1, honorMachinePins=False)
    placed = result["operations"][0]
    assert placed["machineUnitId"] == shop["gios"].id
    assert "Lathe #2 is Gio Reyes's unit" in (placed["message"] or "")


# --- 3. Faster unit: suggested, never applied -------------------------------

def test_pair_suggestions_list_each_worker_and_unit_with_next_free(client, shop):
    _booked(shop, shop["ben"], shop["open"], _at(MON, 8), _at(MON, 12))
    body = {
        "machineTypeId": shop["lathe"].id,
        "pairs": True,
        "estimatedHours": 2,
        "excludeJobId": _job(shop).id,
    }
    res = client.post("/api/v1/workers/suggest", json=body, headers=_headers(shop["admin"]))
    assert res.status_code == 200, res.get_json()
    rows = res.get_json()["suggestions"]
    gio_pairs = [s for s in rows if s["fullName"] == "Gio Reyes"]
    labels = {s["machineUnitLabel"] for s in gio_pairs}
    assert labels == {"Lathe #2", "Lathe #8"}
    own = next(s for s in gio_pairs if s["machineUnitLabel"] == "Lathe #2")
    open_u = next(s for s in gio_pairs if s["machineUnitLabel"] == "Lathe #8")
    assert own["isUnitOperator"] is True
    assert own["components"]["operator"] == 1
    assert open_u["components"]["operator"] == 0
    assert own["nextFreeStart"]
    assert open_u["nextFreeStart"]


def test_a_late_job_gets_a_faster_worker_and_unit_pair(client, shop):
    _booked(shop, shop["ben"], shop["gios"], _at(MON, 8), _at(MON + timedelta(days=2), 17))
    job = _job(shop)
    job.due_date = MON
    db.session.commit()
    result = _propose(client, shop, job, [_op(shop, shop["gio"], shop["gios"], hours=2)])
    op = result["operations"][0]
    assert op["machineUnitId"] == shop["gios"].id
    pair = op.get("fasterPair")
    assert pair, result
    assert pair["machineUnitId"] == shop["open"].id
    assert _parse(pair["projectedCompletion"]) < _parse(result["projectedCompletion"])


def test_a_much_faster_unit_is_suggested_without_swapping(client, shop):
    _booked(shop, shop["ben"], shop["gios"], _at(MON, 8), _at(MON + timedelta(days=1), 17))
    job = _job(shop)
    result = _propose(client, shop, job, [_op(shop, shop["gio"], shop["gios"])])
    op = result["operations"][0]
    assert op["machineUnitId"] == shop["gios"].id
    faster = op["fasterUnit"]
    assert faster["machineUnitId"] == shop["open"].id
    assert faster["ownership"] == "OPEN"
    assert _parse(faster["scheduledStart"]) == _at(MON, 8)
    # Mon 8:00 to Wed 8:00 is two working days of Gio's (lunch excluded).
    assert faster["hoursEarlier"] == pytest.approx(16, abs=2)
    assert _parse(faster["projectedCompletion"]) < _parse(faster["currentProjectedCompletion"])
    assert faster["currentProjectedCompletion"] == result["projectedCompletion"]


def test_no_suggestion_when_the_gain_is_small(client, shop):
    _booked(shop, shop["ben"], shop["gios"], _at(MON, 8), _at(MON, 10))
    job = _job(shop)
    result = _propose(client, shop, job, [_op(shop, shop["gio"], shop["gios"])])
    assert not result["operations"][0].get("fasterUnit")


def test_a_unit_that_cannot_be_used_at_all_gets_a_suggestion(client, shop):
    from app.models.operation_time import DowntimeCategory, MachineDowntime

    db.session.add(MachineDowntime(machine_unit_id=shop["gios"].id, started_at=_at(MON, 7),
                                   category=DowntimeCategory.MECHANICAL_FAILURE, reason="Spindle",
                                   reported_by_id=shop["admin"].id))
    db.session.commit()
    job = _job(shop)
    result = _propose(client, shop, job, [_op(shop, shop["gio"], shop["gios"])])
    op = result["operations"][0]
    assert op["scheduled"] is False
    assert op["fasterUnit"]["machineUnitId"] == shop["open"].id
    assert op["fasterUnit"]["currentProjectedCompletion"] is None


# --- 5. Same-day jobs: PO date, then created ---------------------------------

def test_jobs_due_the_same_day_are_listed_by_po_date_then_created(client, shop):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    due = MON + timedelta(days=5)
    rows = {}
    for key, po, created in (("no_po", None, 1), ("po_late", date(2026, 3, 5), 2),
                             ("po_early_b", date(2026, 3, 1), 4), ("po_early_a", date(2026, 3, 1), 3)):
        rows[key] = _job(shop, po_date=po, created_at=base + timedelta(days=created))
        rows[key].due_date = due
    db.session.commit()
    res = client.get("/api/v1/job-orders?scope=drafts", headers=_headers(shop["admin"]))
    assert res.status_code == 200
    payload = res.get_json()
    items = payload if isinstance(payload, list) else payload.get("items") or payload.get("jobOrders")
    ids = [j["id"] for j in items if j["id"] in {r.id for r in rows.values()}]
    assert ids == [rows[k].id for k in ("po_early_a", "po_early_b", "po_late", "no_po")]


# --- Date-required gate for suggestions while planning ------------------------

def _gate_suggest(client, shop, job, **extra):
    body = {"machineTypeId": shop["lathe"].id, "pairs": True, "estimatedHours": 2,
            "excludeJobId": job.id, **extra}
    res = client.post("/api/v1/workers/suggest", json=body, headers=_headers(shop["admin"]))
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _busy_days(shop, worker, unit, days, start_day=None):
    first = start_day or shop_now().date()
    for d in range(days):
        day = first + timedelta(days=d)
        _booked(shop, worker, unit, _at(day, 6), _at(day, 23))


def _due(job, day):
    job.due_date = day
    db.session.commit()
    return job


def test_equal_scores_go_to_the_unit_free_sooner(client, shop):
    nine = MachineUnit(machine_type_id=shop["lathe"].id, label="Lathe #9", active=True)
    db.session.add(nine)
    db.session.commit()
    _busy_days(shop, shop["ana"], shop["open"], 5)  # Lathe #8 busy all week
    job = _due(_job(shop), shop_now().date() + timedelta(days=30))
    rows = _gate_suggest(client, shop, job)["suggestions"]
    ben = [s["machineUnitLabel"] for s in rows if s["fullName"] == "Ben Cruz"]
    assert ben[:2] == ["Lathe #9", "Lathe #8"]


def test_a_busy_operator_stays_first_when_the_job_can_still_make_its_date(client, shop):
    _busy_days(shop, shop["gio"], shop["gios"], 3)
    job = _due(_job(shop), shop_now().date() + timedelta(days=30))
    rows = _gate_suggest(client, shop, job)["suggestions"]
    assert (rows[0]["fullName"], rows[0]["machineUnitLabel"]) == ("Gio Reyes", "Lathe #2")
    assert rows[0]["meetsDate"] is True


def test_a_pair_that_would_miss_the_date_is_ranked_out(client, shop):
    _busy_days(shop, shop["gio"], shop["gios"], 6)
    job = _due(_job(shop), shop_now().date() + timedelta(days=3))
    body = _gate_suggest(client, shop, job)
    rows = body["suggestions"]
    assert body["dateGate"]["anyMeetsDate"] is True
    assert rows[0]["fullName"] != "Gio Reyes" and rows[0]["meetsDate"] is True
    gio = next(s for s in rows if s["fullName"] == "Gio Reyes" and s["machineUnitLabel"] == "Lathe #2")
    assert gio["meetsDate"] is False
    # Every row that meets the date comes before every row that does not.
    flags = [s["meetsDate"] for s in rows]
    assert flags == sorted(flags, reverse=True)


def test_when_nobody_can_make_the_date_the_closest_finish_comes_first(client, shop):
    for person, unit in ((shop["gio"], shop["gios"]), (shop["ana"], shop["open"])):
        _busy_days(shop, person, unit, 6)
    _busy_days(shop, shop["ben"], shop["gios"], 2, start_day=shop_now().date() + timedelta(days=8))
    job = _due(_job(shop), shop_now().date() + timedelta(days=2))
    body = _gate_suggest(client, shop, job)
    rows = body["suggestions"]
    assert body["dateGate"]["anyMeetsDate"] is False
    ends = [s["nextFreeEnd"] for s in rows]
    assert ends == sorted(ends)


def test_later_steps_leave_room_before_the_date(client, shop):
    """A step that fits on its own but leaves no time for the later steps
    does not meet the date."""
    job = _due(_job(shop), shop_now().date() + timedelta(days=1))
    loose = _gate_suggest(client, shop, job)["suggestions"]
    tight = _gate_suggest(client, shop, job, hoursAfter=40)["suggestions"]
    assert any(s["meetsDate"] for s in loose)
    assert not any(s["meetsDate"] for s in tight)


def test_a_step_starts_after_where_the_earlier_steps_really_land(client, shop):
    """The earlier step's unit is busy for days: the later step's free-from
    follows the earlier step's real place on the calendar, not its target hours."""
    _busy_days(shop, shop["gio"], shop["gios"], 3)
    job = _due(_job(shop), shop_now().date() + timedelta(days=30))
    earlier = [{**_op(shop, shop["gio"], shop["gios"]), "operationTypeId": None}]
    by_hours = _gate_suggest(client, shop, job, hoursBefore=2)["suggestions"]
    placed = _gate_suggest(client, shop, job, hoursBefore=2, stepsBefore=earlier)["suggestions"]

    def ben_start(rows):
        return min(_parse(s["nextFreeStart"]) for s in rows if s["fullName"] == "Ben Cruz")

    busy_until = _at(shop_now().date() + timedelta(days=2), 23)
    assert ben_start(by_hours) < busy_until
    assert ben_start(placed) >= busy_until


def test_earlier_steps_without_a_crew_fall_back_to_their_target_hours(client, shop):
    job = _due(_job(shop), shop_now().date() + timedelta(days=30))
    earlier = [{"sequenceNo": 1, "operationName": "Facing", "estimatedHours": 2,
                "machineTypeId": shop["lathe"].id}]
    by_hours = _gate_suggest(client, shop, job, hoursBefore=2)["suggestions"]
    unplaced = _gate_suggest(client, shop, job, hoursBefore=2, stepsBefore=earlier)["suggestions"]
    assert [s["nextFreeStart"] for s in by_hours] == [s["nextFreeStart"] for s in unplaced]
