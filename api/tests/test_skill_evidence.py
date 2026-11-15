"""Skill evidence: suggested level rules and the per-machine record."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.extensions import db
from app.models.job_order import JobOrder, JobOrderStatus
from app.models.machine import MachineType
from app.models.operation import JobOperation, OperationStatus
from app.models.user import User, UserRole
from app.models.worker_skill import WorkerSkill
from app.services.skill_evidence_service import suggest_level, worker_skill_evidence


def test_no_suggestion_below_minimum_history():
    level, reason = suggest_level(4, 0, 110.0)
    assert level is None
    assert "Not enough history" in reason


def test_helper_only_suggests_level_one():
    assert suggest_level(0, 6, None)[0] == 1


def test_volume_sets_the_ceiling():
    assert suggest_level(5, 0, 100.0)[0] == 3
    assert suggest_level(15, 0, 100.0)[0] == 4
    assert suggest_level(30, 0, 100.0)[0] == 5


def test_speed_against_peers_places_the_level():
    assert suggest_level(15, 0, 90.0)[0] == 3
    assert suggest_level(15, 0, 80.0)[0] == 2
    assert suggest_level(5, 0, 90.0)[0] == 2


def _setup(app):
    from datetime import date

    from app.extensions import bcrypt
    from app.models.client import Client
    from app.models.job_order import JobType, PartCondition
    from app.models.worker_profile import WorkerProfile

    admin = User(
        email="admin.ev@test.local",
        password_hash=bcrypt.generate_password_hash("Admin123!").decode("utf-8"),
        full_name="Admin",
        role=UserRole.ADMIN,
        active=True,
    )
    worker = User(
        email="worker.ev@test.local",
        password_hash=bcrypt.generate_password_hash("Worker123!").decode("utf-8"),
        full_name="Worker Ev",
        role=UserRole.PRODUCTION_WORKER,
        active=True,
    )
    db.session.add_all([admin, worker])
    db.session.flush()
    db.session.add(WorkerProfile(user_id=worker.id))
    mt = MachineType(code="LTH", name="Lathe Machine")
    client = Client(name="Test Client")
    db.session.add_all([mt, client])
    db.session.flush()
    job = JobOrder(
        client_id=client.id,
        title="Shaft",
        due_date=date(2026, 12, 1),
        status=JobOrderStatus.IN_PROGRESS,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        created_by_id=admin.id,
    )
    db.session.add(job)
    db.session.flush()
    return admin, worker, mt, job


def test_evidence_counts_lead_ops_and_flags(app):
    admin, worker, mt, job = _setup(app)
    db.session.add(WorkerSkill(worker_id=worker.id, machine_type_id=mt.id, proficiency=5, is_primary=True))
    now = datetime.now(timezone.utc)
    for i in range(6):
        op = JobOperation(
            job_order_id=job.id,
            sequence_no=i + 1,
            operation_name="Turning",
            machine_type_id=mt.id,
            assigned_worker_id=worker.id,
            estimated_hours=Decimal("4"),
            actual_worked_hours=Decimal("4"),
            actual_start=now - timedelta(days=10 + i, hours=4),
            actual_end=now - timedelta(days=10 + i),
            status=OperationStatus.COMPLETED,
        )
        db.session.add(op)
    db.session.commit()

    ev = worker_skill_evidence(worker.id)
    row = next(m for m in ev["machineTypes"] if m["machineTypeId"] == mt.id)
    assert row["leadOps"] == 6
    assert row["rated"] == 5
    # Six lead operations put the ceiling at 3; rated 5 is two away.
    assert row["suggestedLevel"] == 3
    assert any(f["kind"] == "mismatch" for f in row["flags"])
    # Saved skills show when they were last reviewed.
    assert ev["lastReviewed"] is not None


def test_evidence_endpoint_is_admin_only(client, app):
    admin, worker, mt, job = _setup(app)
    db.session.commit()
    worker_id = worker.id

    def token(email, password):
        return client.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()[
            "accessToken"
        ]

    admin_t = token("admin.ev@test.local", "Admin123!")
    worker_t = token("worker.ev@test.local", "Worker123!")
    ok = client.get(f"/api/v1/workers/{worker_id}/skill-evidence", headers={"Authorization": f"Bearer {admin_t}"})
    assert ok.status_code == 200
    assert "machineTypes" in ok.get_json()
    denied = client.get(
        f"/api/v1/workers/{worker_id}/skill-evidence", headers={"Authorization": f"Bearer {worker_t}"}
    )
    assert denied.status_code == 403
