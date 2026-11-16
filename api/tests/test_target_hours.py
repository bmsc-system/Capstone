"""Target hours suggested from finished work (planning page prefill)."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import create_app
from app.config import Config
from app.extensions import bcrypt, db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus
from app.models.operation import JobOperation, OperationStatus
from app.models.user import User, UserRole, UserStatus
from app.models.worker_skill import OperationType
from app.services.target_hours_service import suggestions_for_job


class LocalTxnConfig(Config):
    TESTING = True
    RATELIMIT_ENABLED = False
    RATELIMIT_STORAGE_URI = "memory://"


@pytest.fixture
def app():
    return create_app(LocalTxnConfig)


@pytest.fixture(autouse=True)
def _rollback_txn(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(db.session, "commit", db.session.flush)
        try:
            yield
        finally:
            db.session.rollback()
            db.session.remove()


@pytest.fixture
def office():
    u = User(
        email="th_office@test.local",
        password_hash=bcrypt.generate_password_hash("Pass123!").decode("utf-8"),
        full_name="th office",
        role=UserRole.OFFICE_STAFF,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(u)
    db.session.flush()
    return u


def _type(code, outsourced=False):
    ot = OperationType(code=code, name=code.title(), active=True, is_outsourced=outsourced)
    db.session.add(ot)
    db.session.flush()
    return ot


def _client(name):
    c = Client(name=name)
    db.session.add(c)
    db.session.flush()
    return c


def _job(office, client, title, qty=None, status=JobOrderStatus.DELIVERED):
    job = JobOrder(
        client_id=client.id,
        title=title,
        due_date=date(2026, 12, 1),
        status=status,
        job_type=JobType.MACHINING,
        material_status=MaterialStatus.NOT_REQUIRED,
        quantity=Decimal(str(qty)) if qty is not None else None,
        created_by_id=office.id,
    )
    db.session.add(job)
    db.session.flush()
    return job


_clock = [datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)]


def _done(job, ot, worked, *, rework_of=None):
    _clock[0] += timedelta(hours=1)
    op = JobOperation(
        job_order_id=job.id,
        sequence_no=1,
        operation_name=ot.name,
        operation_type_id=ot.id,
        status=OperationStatus.COMPLETED,
        estimated_hours=Decimal("1"),
        actual_worked_hours=Decimal(str(worked)) if worked is not None else None,
        actual_end=_clock[0],
        rework_of_operation_id=rework_of,
    )
    db.session.add(op)
    db.session.flush()
    return op


def test_same_job_before_uses_its_hours_scaled_by_quantity(office):
    turn = _type("TH_TURN_A")
    acme = _client("TH Acme")
    past = _job(office, acme, "Drive  Shaft ", qty=5)
    _done(past, turn, 2)
    new = _job(office, acme, "drive shaft", qty=10, status=JobOrderStatus.DRAFT)

    hint = suggestions_for_job(new)[turn.id]

    assert hint["basis"] == "SAME_JOB"
    assert hint["hours"] == 4.0
    assert hint["jobNumber"] == past.job_number


def test_same_title_for_another_client_is_not_the_same_job(office):
    turn = _type("TH_TURN_B")
    past = _job(office, _client("TH One"), "Drive shaft", qty=5)
    _done(past, turn, 2)
    new = _job(office, _client("TH Two"), "Drive shaft", qty=10, status=JobOrderStatus.DRAFT)

    assert turn.id not in suggestions_for_job(new)


def test_otherwise_the_median_of_past_jobs_of_the_type(office):
    mill = _type("TH_MILL")
    c = _client("TH Median")
    for i, worked in enumerate([2, 3, 10]):
        _done(_job(office, c, f"Part {i}"), mill, worked)
    new = _job(office, c, "New part", status=JobOrderStatus.DRAFT)

    hint = suggestions_for_job(new)[mill.id]

    assert hint == {"hours": 3.0, "basis": "PAST_JOBS", "samples": 3}


def test_fewer_than_three_past_jobs_gives_no_suggestion(office):
    weld = _type("TH_WELD")
    c = _client("TH Few")
    _done(_job(office, c, "A"), weld, 2)
    _done(_job(office, c, "B"), weld, 3)
    new = _job(office, c, "C", status=JobOrderStatus.DRAFT)

    assert weld.id not in suggestions_for_job(new)


def test_rework_outside_work_and_missing_hours_do_not_count(office):
    grind = _type("TH_GRIND")
    plating = _type("TH_PLATING", outsourced=True)
    c = _client("TH Skip")
    first = _done(_job(office, c, "A"), grind, 2)
    _done(_job(office, c, "B"), grind, 9, rework_of=first.id)
    _done(_job(office, c, "C"), grind, None)
    _done(_job(office, c, "D"), grind, 2.5)
    for title in ("E", "F", "G"):
        _done(_job(office, c, title), plating, 24)
    new = _job(office, c, "H", status=JobOrderStatus.DRAFT)

    hints = suggestions_for_job(new)

    assert grind.id not in hints  # only two real samples
    assert plating.id not in hints


def test_hours_round_to_the_quarter_hour(office):
    bore = _type("TH_BORE")
    c = _client("TH Round")
    past = _job(office, c, "Hub", qty=3)
    _done(past, bore, 1.1)
    new = _job(office, c, "Hub", qty=4, status=JobOrderStatus.DRAFT)

    assert suggestions_for_job(new)[bore.id]["hours"] == 1.5  # 1.47 -> 1.5
