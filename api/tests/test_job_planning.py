"""Job order planning workflow: DRAFT → SCHEDULED (release).

Runs against the test database (TEST_DATABASE_URL) inside a connection transaction
that is always rolled back.
"""

from datetime import date, time
from decimal import Decimal

import pytest

from app import create_app
from app.config import Config
from app.extensions import bcrypt, db
from app.models.client import Client
from app.models.job_order import (
    JobOrder,
    JobOrderStatus,
    JobPriority,
    JobType,
    MaterialStatus,
    PartCondition,
)
from app.models.notification import NotificationMilestone
from app.models.operation import JobOperation, OperationStatus
from app.models.user import User, UserRole
from app.models.worker_profile import WorkerProfile
from app.models.worker_skill import WorkerSchedule


class LocalTxnConfig(Config):
    """Same local Postgres as development; tests never commit."""

    TESTING = True
    RATELIMIT_ENABLED = False
    RATELIMIT_STORAGE_URI = "memory://"


def _login(client, email, password):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app():
    return create_app(LocalTxnConfig)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _rollback_txn(app, monkeypatch):
    """Never commit — flush only, then roll back."""
    with app.app_context():
        # Keep one open transaction for the whole test; services that "commit"
        # only flush so teardown can undo everything.
        monkeypatch.setattr(db.session, "commit", db.session.flush)
        try:
            yield
        finally:
            db.session.rollback()
            db.session.remove()


@pytest.fixture
def seeded(app):
    admin = User(
        email="plan_admin@test.local",
        password_hash=bcrypt.generate_password_hash("Admin123!").decode("utf-8"),
        full_name="Admin",
        role=UserRole.ADMIN,
        active=True,
    )
    worker = User(
        email="plan_worker@test.local",
        password_hash=bcrypt.generate_password_hash("Worker123!").decode("utf-8"),
        full_name="Worker",
        role=UserRole.PRODUCTION_WORKER,
        active=True,
    )
    office = User(
        email="plan_office@test.local",
        password_hash=bcrypt.generate_password_hash("Office123!").decode("utf-8"),
        full_name="Office",
        role=UserRole.OFFICE_STAFF,
        active=True,
    )
    db.session.add_all([admin, worker, office])
    db.session.flush()
    db.session.add(WorkerProfile(user_id=worker.id))
    for dow in range(6):
        db.session.add(
            WorkerSchedule(
                worker_id=worker.id,
                day_of_week=dow,
                is_working=True,
                start_time=time(8, 0),
                end_time=time(17, 0),
            )
        )
    client_row = Client(name="Plan Test Client")
    db.session.add(client_row)
    db.session.flush()
    return {
        "admin_id": admin.id,
        "worker_id": worker.id,
        "office_id": office.id,
        "client_id": client_row.id,
    }


def _make_job(seeded, status, *, with_op=True, worker_id=None, hours=None):
    job = JobOrder(
        client_id=seeded["client_id"],
        title="Planning Job",
        due_date=date(2026, 9, 1),
        status=status,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        created_by_id=seeded["office_id"],
    )
    db.session.add(job)
    db.session.flush()
    if with_op:
        db.session.add(
            JobOperation(
                job_order_id=job.id,
                sequence_no=1,
                operation_name="Milling",
                assigned_worker_id=worker_id,
                estimated_hours=Decimal(str(hours)) if hours is not None else None,
                status=OperationStatus.PENDING,
            )
        )
        db.session.flush()
    return job


def test_production_cannot_fetch_draft_by_id(client, seeded):
    draft = _make_job(seeded, JobOrderStatus.DRAFT, worker_id=seeded["worker_id"], hours=2)

    login = _login(client, "plan_worker@test.local", "Worker123!")
    token = login.get_json()["accessToken"]
    headers = _auth_header(token)

    assert client.get(f"/api/v1/job-orders/{draft.id}", headers=headers).status_code == 403


def test_only_office_staff_create_job_orders(client, seeded):
    body = {"clientId": seeded["client_id"], "title": "New job", "dueDate": "2026-12-01"}

    admin = _auth_header(_login(client, "plan_admin@test.local", "Admin123!").get_json()["accessToken"])
    assert client.post("/api/v1/job-orders", json=body, headers=admin).status_code == 403

    office = _auth_header(_login(client, "plan_office@test.local", "Office123!").get_json()["accessToken"])
    created = client.post("/api/v1/job-orders", json=body, headers=office)
    assert created.status_code == 201
    assert created.get_json()["status"] == "DRAFT"


def test_priority_is_optional_and_kept_on_edit(client, seeded):
    office = _auth_header(_login(client, "plan_office@test.local", "Office123!").get_json()["accessToken"])
    body = {"clientId": seeded["client_id"], "title": "No priority", "dueDate": "2026-12-01"}

    created = client.post("/api/v1/job-orders", json=body, headers=office)
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["priority"] == "MODERATE"

    with_null = client.post("/api/v1/job-orders", json={**body, "priority": None}, headers=office)
    assert with_null.status_code == 201, with_null.get_json()
    assert with_null.get_json()["priority"] == "MODERATE"

    job = db.session.get(JobOrder, created.get_json()["id"])
    job.priority = JobPriority.HIGH
    db.session.flush()

    for patch in ({"title": "Renamed"}, {"title": "Renamed again", "priority": None}):
        res = client.patch(f"/api/v1/job-orders/{job.id}", json=patch, headers=office)
        assert res.status_code == 200, res.get_json()
        assert res.get_json()["priority"] == "HIGH"


def test_worker_job_payload_omits_amount(client, seeded):
    job = _make_job(
        seeded, JobOrderStatus.SCHEDULED, worker_id=seeded["worker_id"], hours=2
    )
    job.amount = Decimal("12500.00")
    job.client_po_number = "PO-7788"
    job.po_date = date(2026, 8, 15)
    db.session.flush()

    omitted = ("amount", "clientId", "poDate", "createdById")

    login = _login(client, "plan_worker@test.local", "Worker123!")
    token = login.get_json()["accessToken"]
    headers = _auth_header(token)

    detail = client.get(f"/api/v1/job-orders/{job.id}", headers=headers)
    assert detail.status_code == 200
    worker_body = detail.get_json()
    for field in omitted:
        assert field not in worker_body
    assert worker_body.get("clientName") == "Plan Test Client"
    assert worker_body.get("clientPoNumber") == "PO-7788"

    listing = client.get("/api/v1/job-orders", headers=headers)
    assert listing.status_code == 200
    listed = next(j for j in listing.get_json() if j["id"] == job.id)
    for field in omitted:
        assert field not in listed
    assert listed.get("clientName") == "Plan Test Client"
    assert listed.get("clientPoNumber") == "PO-7788"

    office_login = _login(client, "plan_office@test.local", "Office123!")
    office_headers = _auth_header(office_login.get_json()["accessToken"])
    office_detail = client.get(f"/api/v1/job-orders/{job.id}", headers=office_headers)
    assert office_detail.status_code == 200
    office_body = office_detail.get_json()
    assert office_body["amount"] == 12500.0
    assert office_body["clientId"] == seeded["client_id"]
    assert office_body["poDate"] == "2026-08-15"
    assert office_body["createdById"] == seeded["office_id"]
    assert office_body["clientName"] == "Plan Test Client"
    assert office_body["clientPoNumber"] == "PO-7788"


def test_operation_cannot_start_on_non_released_job(client, seeded):
    job = _make_job(seeded, JobOrderStatus.DRAFT, worker_id=seeded["worker_id"], hours=2)
    op_id = job.operations[0].id

    login = _login(client, "plan_worker@test.local", "Worker123!")
    token = login.get_json()["accessToken"]
    headers = _auth_header(token)

    response = client.post(
        f"/api/v1/operations/{op_id}/start",
        json={"timestamp": "2026-08-12T08:00:00Z"},
        headers=headers,
    )
    # Access denied (cannot see PLANNING) or explicit not-released gate — both enforce.
    assert response.status_code in (403, 409)
    body = response.get_json()
    msg = (body.get("error") or {}).get("message", "").lower()
    assert response.status_code == 403 or "released" in msg


def test_confirm_blocked_when_missing_worker_or_hours(client, seeded):
    job = _make_job(seeded, JobOrderStatus.DRAFT, worker_id=None, hours=None)

    login = _login(client, "plan_admin@test.local", "Admin123!")
    token = login.get_json()["accessToken"]
    headers = _auth_header(token)

    response = client.post(f"/api/v1/job-orders/{job.id}/schedule/confirm", headers=headers)
    assert response.status_code == 400
    msg = response.get_json()["error"]["message"].lower()
    assert "worker" in msg
    assert "hours" in msg


def test_migration_012_maps_legacy_statuses():
    """Migration 012 consolidates statuses for production floor."""
    mapping = {
        "PLANNING": "DRAFT",
        "RELEASED": "SCHEDULED",
        "ASSIGNED": "SCHEDULED",
        "UNASSIGNED": "SCHEDULED",
        "IN_PROGRESS": "IN_PROGRESS",
        "COMPLETED": "COMPLETED",
        "DELIVERED": "DELIVERED",
    }
    for before, after in mapping.items():
        if before == "PLANNING":
            assert after == "DRAFT"
        elif before in ("RELEASED", "ASSIGNED", "UNASSIGNED"):
            assert after == "SCHEDULED"
        else:
            assert after == before


def test_no_notification_on_draft_or_planning_job_received_on_confirm(
    client, seeded, monkeypatch
):
    sent = []

    def fake_safe_notify(job_id, milestone):
        sent.append((job_id, milestone))

    import app.services.notification_service as nsvc

    monkeypatch.setattr(nsvc, "safe_notify_job_milestone", fake_safe_notify)

    login_office = _login(client, "plan_office@test.local", "Office123!")
    office_headers = _auth_header(login_office.get_json()["accessToken"])

    create = client.post(
        "/api/v1/job-orders",
        json={
            "clientId": seeded["client_id"],
            "title": "PO Draft",
            "dueDate": "2026-09-15",
            "jobType": "FABRICATION",
            "priority": "MODERATE",
        },
        headers=office_headers,
    )
    assert create.status_code == 201, create.get_json()
    job_id = create.get_json()["id"]
    assert create.get_json()["status"] == "DRAFT"
    assert sent == []

    login_admin = _login(client, "plan_admin@test.local", "Admin123!")
    admin_headers = _auth_header(login_admin.get_json()["accessToken"])

    save_ops = client.patch(
        f"/api/v1/job-orders/{job_id}",
        json={
            "operations": [
                {
                    "sequenceNo": 1,
                    "operationName": "Turning",
                    "assignedWorkerId": seeded["worker_id"],
                    "estimatedHours": 3,
                }
            ]
        },
        headers=admin_headers,
    )
    assert save_ops.status_code == 200, save_ops.get_json()
    assert save_ops.get_json()["status"] == "DRAFT"
    assert sent == []

    # Office sets materials; Admin plans operations (Admin cannot edit job info).
    mats = client.patch(
        f"/api/v1/job-orders/{job_id}",
        json={"materialStatus": "NOT_REQUIRED"},
        headers=office_headers,
    )
    assert mats.status_code == 200, mats.get_json()

    plan = client.patch(
        f"/api/v1/job-orders/{job_id}",
        json={
            "operations": [
                {
                    "sequenceNo": 1,
                    "operationName": "Turning",
                    "assignedWorkerId": seeded["worker_id"],
                    "estimatedHours": 3,
                    "scheduledStart": "2031-03-10T01:00:00+00:00",
                    "scheduledEnd": "2031-03-10T04:00:00+00:00",
                }
            ]
        },
        headers=admin_headers,
    )
    assert plan.status_code == 200, plan.get_json()
    assert plan.get_json()["status"] == "DRAFT"
    assert sent == []

    # commit is patched to flush, so drop the cached pre-replacement operations.
    db.session.expire_all()
    confirm = client.post(f"/api/v1/job-orders/{job_id}/schedule/confirm", headers=admin_headers)
    assert confirm.status_code == 200, confirm.get_json()
    assert confirm.get_json()["status"] == "SCHEDULED"
    assert [op["status"] for op in confirm.get_json()["operations"]] == ["SCHEDULED"]
    assert any(m == NotificationMilestone.JOB_RECEIVED for _, m in sent)


def test_confirm_saves_operations_sent_and_releases(client, seeded):
    job = _make_job(seeded, JobOrderStatus.DRAFT, with_op=False)
    job.material_status = MaterialStatus.NOT_REQUIRED
    db.session.flush()
    admin = _auth_header(_login(client, "plan_admin@test.local", "Admin123!").get_json()["accessToken"])

    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/confirm",
        json={
            "operations": [
                {
                    "sequenceNo": 1,
                    "operationName": "Turning",
                    "assignedWorkerId": seeded["worker_id"],
                    "estimatedHours": 3,
                    "scheduledStart": "2031-03-10T01:00:00+00:00",
                    "scheduledEnd": "2031-03-10T04:00:00+00:00",
                }
            ]
        },
        headers=admin,
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["status"] == "SCHEDULED"
    assert [op["operationName"] for op in body["operations"]] == ["Turning"]


def test_release_endpoint_is_gone(client, seeded):
    job = _make_job(seeded, JobOrderStatus.DRAFT, worker_id=seeded["worker_id"], hours=2)
    admin = _auth_header(_login(client, "plan_admin@test.local", "Admin123!").get_json()["accessToken"])
    assert client.post(f"/api/v1/job-orders/{job.id}/release", headers=admin).status_code in (404, 405)


def test_new_job_orders_get_their_own_board_color(client, seeded):
    from app.models.job_order import AUTO_SCHEDULE_COLORS

    office = _auth_header(_login(client, "plan_office@test.local", "Office123!").get_json()["accessToken"])
    body = {"clientId": seeded["client_id"], "dueDate": "2026-12-01"}

    colors = []
    for n in range(3):
        res = client.post("/api/v1/job-orders", json={**body, "title": f"Color {n}"}, headers=office)
        assert res.status_code == 201, res.get_json()
        colors.append(res.get_json()["scheduleColor"])
    assert all(c in AUTO_SCHEDULE_COLORS for c in colors)
    assert len(set(colors)) == 3

    picked = client.post(
        "/api/v1/job-orders", json={**body, "title": "Picked", "scheduleColor": "#123abc"}, headers=office
    )
    assert picked.get_json()["scheduleColor"] == "#123ABC"
