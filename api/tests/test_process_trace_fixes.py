"""Redo, release, breakdown, operation order, delete, and planning-save rules.

Uses the bmsc_test database from conftest (schema built from the models).
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from app.extensions import bcrypt, db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.machine import MachineType, MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.models.operation_time import OperationPauseReason, OperationTimeEvent, OperationTimeLog
from app.models.supplier import Supplier
from app.models.user import User, UserRole, UserStatus
from app.models.worker_profile import WorkerProfile
from app.models.worker_skill import OperationType, WorkerSchedule
from app.services.schedule_calendar import shop_local_to_utc, shop_now


def _user(email, role, *, active=True):
    user = User(
        email=email,
        password_hash=bcrypt.generate_password_hash("Passw0rd!").decode("utf-8"),
        full_name=email.split("@")[0],
        role=role,
        status=UserStatus.ACTIVE,
        active=active,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _headers(user):
    token = create_access_token(identity=user.id, additional_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def shop(app):
    admin = _user("pt_admin@test.local", UserRole.ADMIN)
    db.session.add(WorkerProfile(user_id=admin.id))
    office = _user("pt_office@test.local", UserRole.OFFICE_STAFF)
    worker = _user("pt_worker@test.local", UserRole.PRODUCTION_WORKER)
    other = _user("pt_other@test.local", UserRole.PRODUCTION_WORKER)
    for u in (admin, worker, other):
        for dow in range(7):
            db.session.add(
                WorkerSchedule(
                    worker_id=u.id,
                    day_of_week=dow,
                    is_working=True,
                    start_time=time(8, 0),
                    end_time=time(17, 0),
                )
            )
    client_row = Client(name="Trace Client")
    db.session.add(client_row)
    db.session.commit()
    return {
        "admin": admin,
        "office": office,
        "worker": worker,
        "other": other,
        "client": client_row,
    }


def _job(shop, status=JobOrderStatus.SCHEDULED, material=MaterialStatus.NOT_REQUIRED):
    job = JobOrder(
        client_id=shop["client"].id,
        title="Trace Job",
        due_date=date(2031, 6, 1),
        status=status,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        material_status=material,
        created_by_id=shop["office"].id,
    )
    db.session.add(job)
    db.session.flush()
    return job


def _op(job, seq, name, *, status=OperationStatus.SCHEDULED, worker=None, **extra):
    op = JobOperation(
        job_order_id=job.id,
        sequence_no=seq,
        operation_name=name,
        estimated_hours=Decimal("1"),
        status=status,
        assigned_worker_id=worker.id if worker else None,
        **extra,
    )
    db.session.add(op)
    db.session.flush()
    return op


def _ops_by_seq(job_id):
    return JobOperation.query.filter_by(job_order_id=job_id).order_by(JobOperation.sequence_no).all()


# ---- Item 1: assigning a pending operation ---------------------------------


def test_admin_assigns_pending_op_to_worker_busy_elsewhere(client, shop):
    """Assigning with no window must not fail because the worker is mid-job elsewhere."""
    busy_job = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    _op(
        busy_job,
        1,
        "Deburring",
        status=OperationStatus.IN_PROGRESS,
        worker=shop["other"],
        actual_start=datetime.now(timezone.utc),
    )
    job = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    _op(job, 1, "Cutting", status=OperationStatus.COMPLETED, worker=shop["worker"])
    pending = _op(job, 2, "Cutting", status=OperationStatus.PENDING)
    db.session.commit()

    res = client.patch(
        f"/api/v1/operations/{pending.id}/assign",
        json={"assignedWorkerId": shop["other"].id},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["assignedWorkerId"] == shop["other"].id


# ---- Item 5: validated re-proposed schedules ------------------------------


def test_apply_schedule_refuses_window_outside_working_hours(client, shop):
    job = _job(shop, status=JobOrderStatus.SCHEDULED)
    op = _op(job, 1, "Welding", worker=shop["worker"])
    db.session.commit()
    day = date.today() + timedelta(days=3)
    start = shop_local_to_utc(day, time(20, 0))
    end = shop_local_to_utc(day, time(21, 0))

    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/apply",
        json={
            "operations": [
                {
                    "id": op.id,
                    "scheduledStart": start.isoformat(),
                    "scheduledEnd": end.isoformat(),
                    "assignedWorkerId": shop["worker"].id,
                }
            ]
        },
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 409, res.get_json()
    assert "outside working hours" in res.get_json()["error"]["message"]


def test_apply_schedule_rejects_an_admin_in_the_crew(client, shop):
    # Only production workers are assigned work; an Admin who also works on
    # the floor has a separate worker account.
    job = _job(shop, status=JobOrderStatus.SCHEDULED)
    op = _op(job, 1, "Welding", worker=shop["worker"])
    lathe = MachineType(code="LATHE_PT", name="Lathe PT")
    db.session.add(lathe)
    db.session.flush()
    unit = MachineUnit(machine_type_id=lathe.id, label="Lathe PT #1", active=True)
    db.session.add(unit)
    op.machine_type_id = lathe.id
    db.session.commit()
    day = date.today() + timedelta(days=3)

    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/apply",
        json={
            "operations": [
                {
                    "id": op.id,
                    "scheduledStart": shop_local_to_utc(day, time(9, 0)).isoformat(),
                    "scheduledEnd": shop_local_to_utc(day, time(10, 0)).isoformat(),
                    "assignedWorkerId": shop["admin"].id,
                    "machineUnitId": unit.id,
                }
            ]
        },
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 400
    db.session.refresh(op)
    assert op.assigned_worker_id == shop["worker"].id


# ---- Item 3: confirm checks the material floor -----------------------------


def _unordered_job_starting(shop, days_ahead):
    db.session.add(Supplier(name="Trace Steel", typical_lead_time_days=5))
    job = _job(shop, status=JobOrderStatus.DRAFT, material=MaterialStatus.TO_ORDER)
    day = shop_now().date() + timedelta(days=days_ahead)
    _op(
        job,
        1,
        "Cutting",
        status=OperationStatus.PENDING,
        worker=shop["worker"],
        scheduled_start=shop_local_to_utc(day, time(9, 0)),
        scheduled_end=shop_local_to_utc(day, time(10, 0)),
    )
    db.session.commit()
    return job


def test_confirm_refuses_start_before_lead_time_floor(client, shop):
    job = _unordered_job_starting(shop, 3)
    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/confirm", headers=_headers(shop["admin"])
    )
    assert res.status_code == 400, res.get_json()
    assert res.get_json()["error"]["code"] == "MATERIAL_NOT_READY"
    db.session.refresh(job)
    assert job.status == JobOrderStatus.DRAFT


def test_confirm_allows_unordered_job_after_lead_time_floor(client, shop):
    job = _unordered_job_starting(shop, 6)
    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/confirm", headers=_headers(shop["admin"])
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "SCHEDULED"


# ---- Item 4: breakdown pauses running work --------------------------------


def test_breakdown_pauses_running_operation(client, shop):
    mtype = MachineType(code="PRESS_PT", name="Press PT")
    db.session.add(mtype)
    db.session.flush()
    unit = MachineUnit(machine_type_id=mtype.id, label="Press #1", active=True)
    db.session.add(unit)
    db.session.flush()
    job = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    started = datetime.now(timezone.utc) - timedelta(hours=1)
    op = _op(
        job,
        1,
        "Pressing",
        status=OperationStatus.IN_PROGRESS,
        worker=shop["worker"],
        machine_type_id=mtype.id,
        machine_unit_id=unit.id,
        actual_start=started,
    )
    db.session.add(
        OperationTimeLog(
            operation_id=op.id,
            worker_id=shop["worker"].id,
            event=OperationTimeEvent.START,
            event_at=started,
        )
    )
    db.session.commit()

    res = client.post(
        f"/api/v1/operations/machine-units/{unit.id}/downtime",
        json={"category": "MECHANICAL_FAILURE"},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 201, res.get_json()

    logs = (
        OperationTimeLog.query.filter_by(operation_id=op.id)
        .order_by(OperationTimeLog.event_at)
        .all()
    )
    assert logs[-1].event == OperationTimeEvent.PAUSE
    assert logs[-1].reason == OperationPauseReason.MACHINE_DOWN
    assert logs[-1].worker_id == shop["worker"].id
    db.session.refresh(op)
    assert op.status == OperationStatus.IN_PROGRESS


# ---- Item 7: operation order ---------------------------------------------


def test_cannot_start_before_earlier_operations_complete(client, shop):
    job = _job(shop, status=JobOrderStatus.SCHEDULED)
    _op(job, 1, "Cutting", worker=shop["other"])
    second = _op(job, 2, "Welding", worker=shop["worker"])
    db.session.commit()

    res = client.post(
        f"/api/v1/operations/{second.id}/start", json={}, headers=_headers(shop["worker"])
    )
    assert res.status_code == 409, res.get_json()
    assert res.get_json()["error"]["code"] == "PRIOR_OPERATION_INCOMPLETE"
    assert "Cutting" in res.get_json()["error"]["message"]


def test_can_start_once_earlier_operations_complete(client, shop):
    job = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    _op(job, 1, "Cutting", status=OperationStatus.COMPLETED, worker=shop["other"])
    second = _op(job, 2, "Welding", worker=shop["worker"])
    db.session.commit()

    res = client.post(
        f"/api/v1/operations/{second.id}/start", json={}, headers=_headers(shop["worker"])
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "IN_PROGRESS"


# ---- Item 8: cancel (hard delete removed) --------------------------------


def test_released_job_can_be_cancelled_not_deleted(client, shop):
    job = _job(shop, status=JobOrderStatus.SCHEDULED)
    _op(job, 1, "Cutting", worker=shop["worker"])
    db.session.commit()

    res = client.delete(f"/api/v1/job-orders/{job.id}", headers=_headers(shop["admin"]))
    assert res.status_code == 405

    res = client.post(
        f"/api/v1/job-orders/{job.id}/cancel",
        headers=_headers(shop["admin"]),
        json={"reason": "CLIENT_CANCELLED"},
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "CANCELLED"
    assert db.session.get(JobOrder, job.id) is not None


def test_pending_job_is_cancelled_not_deleted(client, shop):
    job = _job(shop, status=JobOrderStatus.DRAFT)
    _op(job, 1, "Cutting", status=OperationStatus.PENDING)
    db.session.commit()
    job_id = job.id

    res = client.post(
        f"/api/v1/job-orders/{job_id}/cancel",
        headers=_headers(shop["office"]),
        json={"reason": "DUPLICATE_OR_ENCODING_ERROR"},
    )
    assert res.status_code == 200, res.get_json()
    db.session.expire_all()
    row = db.session.get(JobOrder, job_id)
    assert row is not None
    assert row.status == JobOrderStatus.CANCELLED


# ---- Items 6 and 9: server-side integrity --------------------------------


def test_part_stage_cannot_be_set(client, shop):
    job = _job(shop, status=JobOrderStatus.SCHEDULED)
    db.session.commit()
    res = client.patch(
        f"/api/v1/job-orders/{job.id}",
        json={"partCondition": "FINISHED"},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 400


def test_office_chooses_not_required_on_draft_but_not_after_release(client, shop):
    draft = _job(shop, status=JobOrderStatus.DRAFT, material=MaterialStatus.TO_ORDER)
    released = _job(shop, status=JobOrderStatus.SCHEDULED, material=MaterialStatus.TO_ORDER)
    db.session.commit()
    res = client.patch(
        f"/api/v1/job-orders/{draft.id}",
        json={"materialStatus": "NOT_REQUIRED"},
        headers=_headers(shop["office"]),
    )
    assert res.status_code == 200
    res = client.patch(
        f"/api/v1/job-orders/{released.id}",
        json={"materialStatus": "NOT_REQUIRED"},
        headers=_headers(shop["office"]),
    )
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "JOB_INFO_LOCKED"


# ---- Item 10: planning save with a busy worker ---------------------------


def test_planning_save_with_worker_busy_on_another_job(client, shop):
    busy_job = _job(shop, status=JobOrderStatus.IN_PROGRESS)
    _op(
        busy_job,
        1,
        "Deburring",
        status=OperationStatus.IN_PROGRESS,
        worker=shop["worker"],
        actual_start=datetime.now(timezone.utc),
    )
    pending = _job(shop, status=JobOrderStatus.DRAFT)
    db.session.commit()

    res = client.patch(
        f"/api/v1/job-orders/{pending.id}",
        json={
            "operations": [
                {
                    "operationName": "Deburring",
                    "assignedWorkerId": shop["worker"].id,
                    "estimatedHours": 1,
                    "scheduledStart": None,
                    "scheduledEnd": None,
                }
            ]
        },
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 200, res.get_json()

# ---- Finishing is the final operation ------------------------------------


def _finishing_type():
    ot = OperationType.query.filter_by(code="FINISHING").first()
    if ot is None:
        ot = OperationType(code="FINISHING", name="Finishing (Bapping)")
        db.session.add(ot)
        db.session.flush()
    return ot


def _save_plan(client, shop, job, operations):
    return client.patch(
        f"/api/v1/job-orders/{job.id}",
        json={"operations": operations},
        headers=_headers(shop["admin"]),
    )


def test_planning_save_keeps_finishing_last(client, shop):
    finishing = _finishing_type()
    draft = _job(shop, status=JobOrderStatus.DRAFT)
    db.session.commit()

    res = _save_plan(
        client,
        shop,
        draft,
        [
            {"operationName": "Cutting", "estimatedHours": 1},
            {"operationTypeId": finishing.id, "operationName": finishing.name, "estimatedHours": 1},
            {"operationName": "Fitting", "estimatedHours": 1},
        ],
    )
    assert res.status_code == 200, res.get_json()
    names = [(o.sequence_no, o.operation_name) for o in _ops_by_seq(draft.id)]
    assert names == [(1, "Cutting"), (2, "Fitting"), (3, "Finishing (Bapping)")]


def test_planning_save_without_finishing_keeps_the_given_order(client, shop):
    draft = _job(shop, status=JobOrderStatus.DRAFT)
    db.session.commit()

    res = _save_plan(
        client,
        shop,
        draft,
        [
            {"operationName": "Welding", "estimatedHours": 1},
            {"operationName": "Cutting", "estimatedHours": 1},
        ],
    )
    assert res.status_code == 200, res.get_json()
    assert [o.operation_name for o in _ops_by_seq(draft.id)] == ["Welding", "Cutting"]
