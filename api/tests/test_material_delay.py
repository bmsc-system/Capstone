"""Automatic material delay rescheduling and the stored delay fields.

Uses the bmsc_test database from conftest (schema built from the models).
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from tests.catalog_helpers import approve_issue, ensure_material
from app.extensions import bcrypt, db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.operation import JobOperation, OperationStatus
from app.models.schedule_move import ScheduleMove
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder
from app.models.user import User, UserRole, UserStatus
from app.models.worker_profile import WorkerProfile
from app.models.worker_skill import WorkerSchedule
from app.services import material_delay_service as delay_service
from app.services.schedule_calendar import (
    SHOP_TZ,
    ensure_utc,
    next_shop_working_day,
    shop_now,
    utc_to_shop,
)


def _user(email, role):
    user = User(
        email=email,
        password_hash=bcrypt.generate_password_hash("Passw0rd!").decode("utf-8"),
        full_name=email.split("@")[0],
        role=role,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    if role == UserRole.PRODUCTION_WORKER:
        db.session.add(WorkerProfile(user_id=user.id))
    return user


def _headers(user):
    token = create_access_token(identity=user.id, additional_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


def _local(day, hh, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=SHOP_TZ)


@pytest.fixture
def shop(app):
    admin = _user("md_admin@test.local", UserRole.ADMIN)
    office = _user("md_office@test.local", UserRole.OFFICE_STAFF)
    worker = _user("md_worker@test.local", UserRole.PRODUCTION_WORKER)
    for dow in range(7):
        db.session.add(
            WorkerSchedule(
                worker_id=worker.id,
                day_of_week=dow,
                is_working=True,
                start_time=time(8, 0),
                end_time=time(17, 0),
            )
        )
    client_row = Client(name="Delay Client")
    slow = Supplier(name="Slow Steel", code="SLS", typical_lead_time_days=10)
    quick = Supplier(name="Quick Steel", code="QKS", typical_lead_time_days=1)
    db.session.add_all([client_row, slow, quick])
    db.session.commit()
    return {
        "admin": admin,
        "office": office,
        "worker": worker,
        "client": client_row,
        "slow": slow,
        "quick": quick,
        "today": shop_now().date(),
    }


def _job(shop, first_start, *, status=JobOrderStatus.SCHEDULED):
    """Scheduled job: two 2-hour operations, the second at 13:00 the same day."""
    job = JobOrder(
        client_id=shop["client"].id,
        title="Delay Job",
        due_date=shop["today"] + timedelta(days=90),
        status=status,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        material_status=MaterialStatus.TO_ORDER,
        created_by_id=shop["office"].id,
    )
    db.session.add(job)
    db.session.flush()
    ops = []
    for seq, start in ((1, first_start), (2, first_start.replace(hour=13))):
        op = JobOperation(
            job_order_id=job.id,
            sequence_no=seq,
            operation_name=f"Op {seq}",
            estimated_hours=Decimal("2"),
            status=OperationStatus.SCHEDULED,
            assigned_worker_id=shop["worker"].id,
            scheduled_start=start,
            scheduled_end=start + timedelta(hours=2),
        )
        db.session.add(op)
        ops.append(op)
    db.session.commit()
    return job, ops


def _draft_order(client, shop, job, supplier):
    res = client.post(
        "/api/v1/supplier-orders/draft-lines",
        json={
            "supplierId": supplier.id,
            "lines": [
                {
                    "jobOrderId": job.id,
                    "materialCatalogId": ensure_material("Plate"), "materialName": "Plate",
                    "unit": "pcs",
                    "quantity": 1,
                    "unitCost": 100,
                }
            ],
        },
        headers=_headers(shop["office"]),
    )
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _issue(client, shop, order_id, issued):
    res = approve_issue(client, f"/api/v1/supplier-orders/{order_id}", {"dateIssued": issued.isoformat()}, _headers(shop["office"]))
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _starts(job):
    db.session.expire_all()
    job = db.session.get(JobOrder, job.id)
    return job, [ensure_utc(op.scheduled_start) for op in job.operations]


def test_issuing_moves_job_later_and_records_delay(client, shop):
    tomorrow = shop["today"] + timedelta(days=1)
    job, ops = _job(shop, _local(tomorrow, 9))
    old_starts = [ensure_utc(op.scheduled_start) for op in ops]
    order = _draft_order(client, shop, job, shop["slow"])
    issued = _issue(client, shop, order["id"], shop["today"])

    job, starts = _starts(job)
    arrival = next_shop_working_day(shop["today"] + timedelta(days=10))
    assert utc_to_shop(starts[0]).date() == arrival
    assert utc_to_shop(starts[0]).time() == time(8, 0)
    assert starts[1] >= starts[0] + timedelta(hours=2)
    assert all(new >= old for new, old in zip(starts, old_starts))

    assert ensure_utc(job.material_delay_original_start) == old_starts[0]
    assert job.material_delay_supplier_order_id == order["id"]
    assert job.material_delay_reason.startswith(f"{issued['poNumber']} issued")
    assert job.material_delayed_at is not None
    assert job.delay_kind == "MATERIAL"
    assert [m.kind for m in ScheduleMove.query.filter_by(job_order_id=job.id)] == ["MATERIAL"]

    body = client.get(f"/api/v1/job-orders/{job.id}", headers=_headers(shop["admin"])).get_json()
    assert body["materialDelay"]["poNumber"] == issued["poNumber"]
    assert body["materialDelay"]["originalStart"] is not None


def test_original_start_is_never_overwritten(client, shop):
    tomorrow = shop["today"] + timedelta(days=1)
    job, ops = _job(shop, _local(tomorrow, 9))
    first_planned = ensure_utc(ops[0].scheduled_start)
    order = _draft_order(client, shop, job, shop["slow"])
    _issue(client, shop, order["id"], shop["today"])
    job, after_issue = _starts(job)

    so = db.session.get(SupplierOrder, order["id"])
    # Match the API: bump then snap to a shop working day (raw +3 can hit Sunday).
    so.expected_delivery_date = next_shop_working_day(
        so.expected_delivery_date + timedelta(days=3)
    )
    db.session.commit()
    outcomes = delay_service.reschedule_for_order(so, delay_service.EXPECTED_DATE_CHANGED)
    assert [o["outcome"] for o in outcomes] == ["MOVED"]

    job, after_edit = _starts(job)
    assert utc_to_shop(after_edit[0]).date() == so.expected_delivery_date
    assert after_edit[0] > after_issue[0]
    assert ensure_utc(job.material_delay_original_start) == first_planned
    assert "expected delivery changed" in job.material_delay_reason


def test_earlier_material_date_never_moves_job_earlier(client, shop):
    tomorrow = shop["today"] + timedelta(days=1)
    job, _ = _job(shop, _local(tomorrow, 9))
    order = _draft_order(client, shop, job, shop["slow"])
    _issue(client, shop, order["id"], shop["today"])
    job, moved = _starts(job)
    reason = job.material_delay_reason

    so = db.session.get(SupplierOrder, order["id"])
    so.expected_delivery_date = shop["today"] + timedelta(days=2)
    db.session.commit()
    outcomes = delay_service.reschedule_for_order(so, delay_service.EXPECTED_DATE_CHANGED)
    assert [o["outcome"] for o in outcomes] == ["UNCHANGED"]
    job, now = _starts(job)
    assert now == moved
    assert job.material_delay_reason == reason


def test_material_in_time_leaves_job_alone(client, shop):
    later = shop["today"] + timedelta(days=5)
    job, ops = _job(shop, _local(later, 9))
    old = [ensure_utc(op.scheduled_start) for op in ops]
    order = _draft_order(client, shop, job, shop["quick"])
    _issue(client, shop, order["id"], shop["today"])
    job, starts = _starts(job)
    assert starts == old
    assert job.material_delayed_at is None
    assert job.material_delay_original_start is None


def test_started_job_never_moves(client, shop):
    tomorrow = shop["today"] + timedelta(days=1)
    job, ops = _job(shop, _local(tomorrow, 9))
    ops[0].actual_start = ops[0].scheduled_start
    ops[0].status = OperationStatus.IN_PROGRESS
    job.status = JobOrderStatus.IN_PROGRESS
    db.session.commit()
    old = [ensure_utc(op.scheduled_start) for op in ops]
    order = _draft_order(client, shop, job, shop["slow"])
    _issue(client, shop, order["id"], shop["today"])
    job, starts = _starts(job)
    assert starts == old
    assert job.material_delayed_at is None


def test_no_slot_leaves_schedule_unchanged(client, shop):
    tomorrow = shop["today"] + timedelta(days=1)
    job, ops = _job(shop, _local(tomorrow, 9))
    old = [ensure_utc(op.scheduled_start) for op in ops]
    WorkerSchedule.query.filter_by(worker_id=shop["worker"].id).update(
        {"is_working": False}
    )
    db.session.commit()
    order = _draft_order(client, shop, job, shop["slow"])
    _issue(client, shop, order["id"], shop["today"])
    job, starts = _starts(job)
    assert starts == old
    assert job.material_delayed_at is None
    assert job.material_delay_reason is None


def test_overdue_at_issue_moves_job_and_receipt_never_moves_it_back(client, shop):
    yesterday = shop["today"] - timedelta(days=1)
    job, ops = _job(shop, _local(yesterday, 9))
    old_first = ensure_utc(ops[0].scheduled_start)
    order = _draft_order(client, shop, job, shop["quick"])
    # Expected two days ago and not received: overdue, so expected tomorrow at the earliest.
    issued = _issue(client, shop, order["id"], shop["today"] - timedelta(days=3))
    job, starts = _starts(job)
    assert utc_to_shop(starts[0]).date() >= shop["today"] + timedelta(days=1)
    assert ensure_utc(job.material_delay_original_start) == old_first
    assert job.material_delay_supplier_order_id == order["id"]
    moved_to = starts[0]

    client.post(
        f"/api/v1/supplier-orders/{issued['id']}/print", headers=_headers(shop["office"])
    )
    res = client.post(
        f"/api/v1/supplier-orders/{issued['id']}/receive",
        json={"receivedDate": shop["today"].isoformat()},
        headers=_headers(shop["office"]),
    )
    assert res.status_code == 200, res.get_json()
    job, starts = _starts(job)
    assert starts[0] == moved_to
    assert ensure_utc(job.material_delay_original_start) == old_first


def test_cancelling_moves_job_to_lead_time_and_blames_cancelled_order(client, shop):
    later = shop["today"] + timedelta(days=3)
    job, ops = _job(shop, _local(later, 9))
    order = _draft_order(client, shop, job, shop["quick"])
    issued = _issue(client, shop, order["id"], shop["today"])
    job, starts = _starts(job)
    assert job.material_delayed_at is None

    res = client.post(
        f"/api/v1/supplier-orders/{order['id']}/cancel",
        json={},
        headers=_headers(shop["office"]),
    )
    assert res.status_code == 200, res.get_json()
    job, starts = _starts(job)
    assert utc_to_shop(starts[0]).date() >= shop["today"] + timedelta(days=10)
    assert job.material_delay_supplier_order_id == order["id"]
    assert job.material_delay_reason.startswith(f"{issued['poNumber']} cancelled")
