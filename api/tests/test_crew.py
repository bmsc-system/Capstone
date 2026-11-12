"""Crews: a lead and up to two helpers per operation, crew-wide clash checks,
recording by any member, efficiency credited to every member, and the
recommendation rules for leads (machine units) and helpers.

Uses the bmsc_test database from conftest (schema built from the models).
"""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.machine import MachineType, MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.models.operation_time import OperationTimeLog
from app.models.user import User, UserRole, UserStatus
from app.models.worker_profile import WorkerProfile
from app.models.worker_skill import OperationType, WorkerSchedule, WorkerSkill
from app.services.analytics_service import efficiency_by_worker
from app.services.schedule_calendar import shop_local_to_utc, shop_now
from app.services.worker_history_service import worker_work_history

_today = shop_now().date()
MON = _today + timedelta(days=(7 - _today.weekday()) % 7 + 7)


def _at(d, hh, mm=0):
    return shop_local_to_utc(d, time(hh, mm))


def _user(email, role, name):
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
    if role in (UserRole.PRODUCTION_WORKER, UserRole.ADMIN):
        db.session.add(WorkerProfile(user_id=user.id))
        for dow in range(6):
            db.session.add(
                WorkerSchedule(
                    worker_id=user.id,
                    day_of_week=dow,
                    is_working=True,
                    start_time=time(8, 0),
                    end_time=time(17, 0),
                )
            )
    return user


def _headers(user):
    token = create_access_token(identity=user.id, additional_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def shop(app):
    admin = _user("crew_admin@test.local", UserRole.ADMIN, "Ada Admin")
    office = _user("crew_office@test.local", UserRole.OFFICE_STAFF, "Olive Office")
    ana = _user("crew_ana@test.local", UserRole.PRODUCTION_WORKER, "Ana Crew")
    ben = _user("crew_ben@test.local", UserRole.PRODUCTION_WORKER, "Ben Crew")
    cara = _user("crew_cara@test.local", UserRole.PRODUCTION_WORKER, "Cara Crew")
    gio = _user("crew_gio@test.local", UserRole.PRODUCTION_WORKER, "Gio Crew")
    lathe = MachineType(code="LATHE_C", name="Lathe", units=2)
    db.session.add(lathe)
    db.session.flush()
    lathe2 = MachineUnit(
        machine_type_id=lathe.id, label="Lathe #2", active=True, default_operator_id=gio.id
    )
    lathe8 = MachineUnit(machine_type_id=lathe.id, label="Lathe #8", active=True)
    types = {
        "TURNING": OperationType(code="TURNING_C", name="Turning", default_machine_type_id=lathe.id),
        "LAYOUT": OperationType(code="LAYOUT_C", name="Layout"),
        "CHECKING": OperationType(code="CHECKING", name="Checking", active=False),
    }
    client_row = Client(name="Crew Client")
    db.session.add_all([lathe2, lathe8, *types.values(), client_row])
    db.session.add_all(
        [
            WorkerSkill(worker_id=ana.id, machine_type_id=lathe.id, proficiency=3),
            WorkerSkill(worker_id=ben.id, machine_type_id=lathe.id, proficiency=4),
            WorkerSkill(worker_id=gio.id, machine_type_id=lathe.id, proficiency=3),
        ]
    )
    db.session.commit()
    return {
        "admin": admin,
        "office": office,
        "ana": ana,
        "ben": ben,
        "cara": cara,
        "gio": gio,
        "lathe": lathe,
        "lathe2": lathe2,
        "lathe8": lathe8,
        "types": types,
        "client": client_row,
    }


def _job(shop, status=JobOrderStatus.SCHEDULED):
    job = JobOrder(
        client_id=shop["client"].id,
        title="Crew Job",
        due_date=MON + timedelta(days=30),
        status=status,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        material_status=MaterialStatus.NOT_REQUIRED,
        created_by_id=shop["office"].id,
    )
    db.session.add(job)
    db.session.flush()
    return job


def _op(job, seq, op_type, *, lead=None, helpers=(), start=None, end=None, status=None):
    op = JobOperation(
        job_order_id=job.id,
        sequence_no=seq,
        operation_name=op_type.name,
        operation_type_id=op_type.id,
        machine_type_id=op_type.default_machine_type_id,
        assigned_worker_id=lead.id if lead else None,
        estimated_hours=Decimal("2"),
        scheduled_start=start,
        scheduled_end=end,
        status=status or OperationStatus.SCHEDULED,
    )
    op.set_helpers([h.id for h in helpers])
    db.session.add(op)
    db.session.commit()
    return op


def _assign(client, shop, op, lead, helpers=None):
    body = {"assignedWorkerId": lead.id}
    if helpers is not None:
        body["helperIds"] = [h.id for h in helpers]
    return client.patch(
        f"/api/v1/operations/{op.id}/assign", json=body, headers=_headers(shop["admin"])
    )


def _suggest(client, shop, op_type, **extra):
    body = {"operationTypeId": op_type.id, "operationName": op_type.name, **extra}
    res = client.post("/api/v1/workers/suggest", json=body, headers=_headers(shop["admin"]))
    assert res.status_code == 200, res.get_json()
    return res.get_json()


# ---- Crew model ------------------------------------------------------------


def test_assign_sets_lead_and_helpers_and_lists_the_crew(client, shop):
    op = _op(_job(shop), 1, shop["types"]["LAYOUT"])
    res = _assign(client, shop, op, shop["ana"], [shop["ben"], shop["cara"]])
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert data["assignedWorkerId"] == shop["ana"].id
    assert data["helperIds"] == [shop["ben"].id, shop["cara"].id]
    assert [m["fullName"] for m in data["crew"]] == ["Ana Crew", "Ben Crew", "Cara Crew"]
    assert [m["isLead"] for m in data["crew"]] == [True, False, False]
    assert data["crewSize"] == 3


def test_at_most_two_helpers_and_never_the_lead(client, shop):
    op = _op(_job(shop), 1, shop["types"]["LAYOUT"])
    res = _assign(client, shop, op, shop["ana"], [shop["ben"], shop["cara"], shop["gio"]])
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == "TOO_MANY_HELPERS"
    res = _assign(client, shop, op, shop["ana"], [shop["ana"]])
    assert res.status_code == 400


def test_machine_lead_needs_the_skill_but_helpers_do_not(client, shop):
    op = _op(_job(shop), 1, shop["types"]["TURNING"])
    res = _assign(client, shop, op, shop["cara"])
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == "WORKER_NOT_QUALIFIED"
    res = _assign(client, shop, op, shop["ana"], [shop["cara"]])
    assert res.status_code == 200, res.get_json()


def test_former_checking_op_takes_any_lead_and_helpers(client, shop):
    """The Admin-only and no-helpers Checking rules are gone."""
    op = _op(_job(shop), 1, shop["types"]["CHECKING"])
    res = _assign(client, shop, op, shop["ben"], [shop["cara"]])
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["helperIds"] == [shop["cara"].id]

    helpers = _suggest(client, shop, shop["types"]["CHECKING"], leadId=shop["ben"].id)
    assert helpers["suggestions"]
    leads = _suggest(client, shop, shop["types"]["CHECKING"])
    assert "Ben Crew" in {s["fullName"] for s in leads["suggestions"]}


# ---- Scheduling: every member is booked ------------------------------------


def _confirm(client, shop, job, operations):
    return client.post(
        f"/api/v1/job-orders/{job.id}/schedule/confirm",
        json={"operations": operations},
        headers=_headers(shop["admin"]),
    )


def _layout_payload(shop, helpers):
    layout = shop["types"]["LAYOUT"]
    return {
        "sequenceNo": 1,
        "operationName": layout.name,
        "operationTypeId": layout.id,
        "assignedWorkerId": shop["ana"].id,
        "helperIds": [h.id for h in helpers],
        "estimatedHours": 2,
        "scheduledStart": _at(MON, 9).isoformat(),
    }


def test_a_helpers_clash_blocks_confirming(client, shop):
    busy = _job(shop)
    _op(busy, 1, shop["types"]["LAYOUT"], lead=shop["ben"], start=_at(MON, 8), end=_at(MON, 12))
    job = _job(shop, status=JobOrderStatus.DRAFT)
    db.session.commit()

    res = _confirm(client, shop, job, [_layout_payload(shop, [shop["ben"]])])
    assert res.status_code == 409, res.get_json()
    err = res.get_json()["error"]
    assert err["code"] == "SCHEDULE_INVALID"
    assert "Ben Crew is already booked" in err["message"], err["message"]

    res = _confirm(client, shop, job, [_layout_payload(shop, [shop["cara"]])])
    assert res.status_code == 200, res.get_json()
    mine = client.get("/api/v1/operations/mine", headers=_headers(shop["cara"]))
    assert [o["jobOrderId"] for o in mine.get_json()] == [job.id]


def test_a_helper_booked_elsewhere_is_busy_for_assignment(client, shop):
    busy = _job(shop)
    _op(busy, 1, shop["types"]["LAYOUT"], lead=shop["ana"], helpers=[shop["ben"]],
        start=_at(MON, 8), end=_at(MON, 12))
    other = _op(_job(shop), 1, shop["types"]["LAYOUT"], start=_at(MON, 10), end=_at(MON, 11))
    res = _assign(client, shop, other, shop["ben"])
    assert res.status_code == 409, res.get_json()
    assert "Ben Crew is unavailable" in res.get_json()["error"]["message"]


# ---- Recording: any member, one shared timer -------------------------------


def test_any_crew_member_can_record_and_each_action_names_who_did_it(client, shop):
    ana, ben, cara = shop["ana"], shop["ben"], shop["cara"]
    op = _op(_job(shop), 1, shop["types"]["LAYOUT"])
    assert _assign(client, shop, op, ana, [ben]).status_code == 200
    url = f"/api/v1/operations/{op.id}"

    assert client.post(f"{url}/start", json={}, headers=_headers(ben)).status_code == 200
    res = client.post(f"{url}/pause", json={"reason": "BREAK"}, headers=_headers(ana))
    assert res.status_code == 200, res.get_json()
    assert client.post(f"{url}/resume", json={}, headers=_headers(ben)).status_code == 200

    outsider = client.post(f"{url}/pause", json={"reason": "BREAK"}, headers=_headers(cara))
    assert outsider.status_code == 403
    assert outsider.get_json()["error"]["code"] == "OPERATION_REASSIGNED"

    res = client.post(f"{url}/complete", json={}, headers=_headers(ana))
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "COMPLETED"

    logs = (
        OperationTimeLog.query.filter_by(operation_id=op.id)
        .order_by(OperationTimeLog.event_at, OperationTimeLog.id)
        .all()
    )
    assert [(log.event.value, log.worker_id) for log in logs] == [
        ("START", ben.id),
        ("PAUSE", ana.id),
        ("RESUME", ben.id),
        ("COMPLETE", ana.id),
    ]


def test_a_member_taken_off_the_crew_is_told_it_was_reassigned(client, shop):
    ana, ben = shop["ana"], shop["ben"]
    op = _op(_job(shop), 1, shop["types"]["LAYOUT"], lead=ana, helpers=[ben])
    assert _assign(client, shop, op, ana, []).status_code == 200
    res = client.post(f"/api/v1/operations/{op.id}/start", json={}, headers=_headers(ben))
    assert res.status_code == 403
    err = res.get_json()["error"]
    assert err["code"] == "OPERATION_REASSIGNED"
    assert "Ana Crew" in err["message"]


# ---- Analytics: every member is credited -----------------------------------


def test_efficiency_and_history_credit_every_crew_member(shop):
    ana, ben = shop["ana"], shop["ben"]
    done = _op(_job(shop), 1, shop["types"]["LAYOUT"], lead=ana, helpers=[ben],
               status=OperationStatus.COMPLETED)
    finished = datetime.now(timezone.utc) - timedelta(days=1)
    done.actual_start = finished - timedelta(hours=3)
    done.actual_end = finished
    done.actual_worked_hours = Decimal("2.5")
    done.variance_pct = Decimal("25")
    db.session.commit()

    rows = {r["workerId"]: r for r in efficiency_by_worker(min_ops=1)["workers"]}
    for person in (ana, ben):
        assert rows[person.id]["operationCount"] == 1
        assert rows[person.id]["totalEstimatedHours"] == 2.0
        assert rows[person.id]["totalActualWorkedHours"] == 2.5

    history = worker_work_history(ben.id)["operations"]["items"]
    assert [(h["isCrew"], h["crewRole"], h["crewSize"]) for h in history] == [(True, "HELPER", 2)]
    assert done.to_dict()["laborHours"] == 5.0


# ---- Recommendations -------------------------------------------------------


def test_a_machines_assigned_operator_ranks_first_with_their_unit(client, shop):
    result = _suggest(client, shop, shop["types"]["TURNING"])
    assert result["mode"] == "MACHINE_LEAD"
    first = result["suggestions"][0]
    assert first["fullName"] == "Gio Crew"
    assert first["machineUnitId"] == shop["lathe2"].id
    assert first["machineUnitLabel"] == "Lathe #2"
    assert first["reason"].startswith("Assigned operator of Lathe #2, skill 3/5")


def test_an_open_unit_is_offered_to_anyone_with_the_skill(client, shop):
    result = _suggest(client, shop, shop["types"]["TURNING"])
    by_name = {s["fullName"]: s for s in result["suggestions"]}
    assert set(by_name) == {"Gio Crew", "Ana Crew", "Ben Crew"}  # Cara has no Lathe skill
    for name in ("Ana Crew", "Ben Crew"):
        assert by_name[name]["machineUnitLabel"] == "Lathe #8"
        assert "Lathe #8 has no assigned operator" in by_name[name]["reason"]


def test_a_busy_operator_drops_out_and_their_unit_opens(client, shop):
    _op(_job(shop), 1, shop["types"]["LAYOUT"], lead=shop["gio"],
        status=OperationStatus.IN_PROGRESS)
    result = _suggest(client, shop, shop["types"]["TURNING"])
    names = [s["fullName"] for s in result["suggestions"]]
    assert "Gio Crew" not in names
    labels = {s["machineUnitLabel"] for s in result["suggestions"]}
    assert labels <= {"Lathe #2", "Lathe #8"}


def test_no_machine_operations_take_everyone(client, shop):
    result = _suggest(client, shop, shop["types"]["LAYOUT"])
    assert result["mode"] == "NO_MACHINE"
    assert result["weights"] == {"efficiency": 0.6, "workload": 0.4}
    names = {s["fullName"] for s in result["suggestions"]}
    assert {"Ana Crew", "Ben Crew", "Cara Crew", "Gio Crew"} <= names
    assert "Ada Admin" not in names  # only production workers are suggested


def test_helper_suggestions_exclude_the_lead_and_need_no_skill(client, shop):
    result = _suggest(client, shop, shop["types"]["TURNING"], leadId=shop["ana"].id)
    assert result["mode"] == "HELPER"
    names = {s["fullName"] for s in result["suggestions"]}
    assert "Ana Crew" not in names
    assert "Cara Crew" in names


def test_history_counts_redos_in_the_total_but_not_the_averages(shop):
    """The summary's total matches the table (redos included); the averages
    use first-time operations only."""
    ana = shop["ana"]
    job = _job(shop)
    finished = datetime.now(timezone.utc) - timedelta(days=1)
    first = _op(job, 1, shop["types"]["LAYOUT"], lead=ana, status=OperationStatus.COMPLETED)
    redo = _op(job, 2, shop["types"]["LAYOUT"], lead=ana, status=OperationStatus.COMPLETED)
    for op in (first, redo):
        op.actual_start = finished - timedelta(hours=2)
        op.actual_end = finished
        op.actual_worked_hours = Decimal("2")
    redo.rework_of_operation_id = first.id
    db.session.commit()

    hist = worker_work_history(ana.id)
    assert hist["summary"]["operationsTotal"] == 2 == hist["operations"]["total"]
    assert hist["summary"]["operationsCompleted"] == 1
    assert hist["summary"]["reworkCount"] == 1


def test_the_admin_is_told_when_an_operation_and_then_the_job_is_finished(client, shop):
    from app.models.staff_alert import StaffAlert

    ana, admin = shop["ana"], shop["admin"]
    job = _job(shop)
    first = _op(job, 1, shop["types"]["LAYOUT"], lead=ana)
    second = _op(job, 2, shop["types"]["LAYOUT"], lead=ana)

    def finish(op):
        url = f"/api/v1/operations/{op.id}"
        assert client.post(f"{url}/start", json={}, headers=_headers(ana)).status_code == 200
        res = client.post(f"{url}/complete", json={}, headers=_headers(ana))
        assert res.status_code == 200, res.get_json()

    finish(first)
    alerts = StaffAlert.query.filter_by(recipient_id=admin.id).all()
    assert [a.kind for a in alerts] == ["OPERATION_DONE"]
    assert alerts[0].title.startswith("Layout finished")
    assert "Next: Layout" in (alerts[0].message or "")
    # Office staff and workers are not told about every operation.
    assert StaffAlert.query.filter_by(recipient_id=shop["office"].id).count() == 0

    finish(second)
    kinds = {a.kind for a in StaffAlert.query.filter_by(recipient_id=admin.id)}
    assert kinds == {"OPERATION_DONE", "JOB_DONE"}

    page = client.get("/api/v1/alerts?limit=1", headers=_headers(admin)).get_json()
    assert len(page["items"]) == 1 and page["total"] == 2 and page["hasMore"] is True
    assert page["items"][0]["kind"] == "JOB_DONE"
    rest = client.get("/api/v1/alerts?limit=1&offset=1", headers=_headers(admin)).get_json()
    assert rest["items"][0]["kind"] == "OPERATION_DONE" and rest["hasMore"] is False
    client.post(f"/api/v1/alerts/{page['items'][0]['id']}/read", headers=_headers(admin))
    unread = client.get("/api/v1/alerts?unread=1", headers=_headers(admin)).get_json()
    assert [a["kind"] for a in unread["items"]] == ["OPERATION_DONE"]
