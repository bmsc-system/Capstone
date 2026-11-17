"""Part condition advancement from completed operation types.

Runs against the test database (TEST_DATABASE_URL) inside a rolled-back session.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import create_app
from app.config import Config
from app.extensions import bcrypt, db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, PartCondition
from app.models.operation import JobOperation, OperationStatus
from app.models.user import User, UserRole, UserStatus
from app.models.worker_skill import OperationType
from app.services.job_order_service import (
    _initial_part_condition,
    advance_part_condition,
)


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


def _ensure_op_type(code: str) -> OperationType:
    existing = OperationType.query.filter_by(code=code).first()
    if existing:
        return existing
    ot = OperationType(code=code, name=code.replace("_", " ").title(), active=True)
    db.session.add(ot)
    db.session.flush()
    return ot


def _job(job_type=JobType.FABRICATION, part_condition=None):
    office = User(
        email="pc_office@test.local",
        password_hash=bcrypt.generate_password_hash("Office123!").decode("utf-8"),
        full_name="Office",
        role=UserRole.OFFICE_STAFF,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(office)
    db.session.flush()
    client = Client(name="PC Test Client")
    db.session.add(client)
    db.session.flush()
    job = JobOrder(
        client_id=client.id,
        title="Part Condition Job",
        due_date=date(2026, 10, 1),
        status=JobOrderStatus.IN_PROGRESS,
        job_type=job_type,
        part_condition=part_condition or _initial_part_condition(job_type),
        created_by_id=office.id,
    )
    db.session.add(job)
    db.session.flush()
    return job


def _add_op(job, code, status=OperationStatus.PENDING, seq=1):
    ot = _ensure_op_type(code)
    op = JobOperation(
        job_order_id=job.id,
        sequence_no=seq,
        operation_name=ot.name,
        operation_type_id=ot.id,
        status=status,
        estimated_hours=Decimal("1"),
    )
    db.session.add(op)
    db.session.flush()
    # Attach relationship for in-memory advance without re-query
    op.operation_type = ot
    return op


def test_initial_part_condition_from_job_type():
    assert _initial_part_condition(JobType.FABRICATION) == PartCondition.RAW_MATERIAL
    assert _initial_part_condition(JobType.MACHINING) == PartCondition.RAW_MATERIAL
    assert _initial_part_condition(JobType.REPAIR) == PartCondition.CLIENT_SUPPLIED_ITEM


def test_blanking_completed_sets_blank():
    job = _job(JobType.FABRICATION)
    _add_op(job, "BLANKING", OperationStatus.COMPLETED)
    _add_op(job, "TURNING", OperationStatus.PENDING, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.BLANK


@pytest.mark.parametrize(
    "code",
    [
        "TURNING",
        "FACING",
        "THREADING",
        "TEETH_CUTTING",
        "SLOTTING",
        "GROOVING",
        "DRILLING",
        "KEYWAY",
        "SPLINE",
        "SURFACE_GRINDING",
    ],
)
def test_machining_op_completed_sets_machined(code):
    job = _job(JobType.FABRICATION)
    _add_op(job, code, OperationStatus.COMPLETED)
    _add_op(job, "FINISHING", OperationStatus.PENDING, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.MACHINED


def test_heat_treatment_completed_sets_heat_treated():
    job = _job(JobType.FABRICATION)
    _add_op(job, "HEAT_TREATMENT", OperationStatus.COMPLETED)
    _add_op(job, "FINISHING", OperationStatus.PENDING, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.HEAT_TREATED


def test_all_operations_completed_sets_finished():
    job = _job(JobType.FABRICATION)
    _add_op(job, "BLANKING", OperationStatus.COMPLETED, seq=1)
    _add_op(job, "TURNING", OperationStatus.COMPLETED, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.FINISHED


def test_never_moves_backwards_when_later_op_completes_out_of_order():
    job = _job(JobType.FABRICATION, part_condition=PartCondition.HEAT_TREATED)
    # Out-of-order blanking complete must not demote HEAT_TREATED
    _add_op(job, "BLANKING", OperationStatus.COMPLETED, seq=1)
    _add_op(job, "HEAT_TREATMENT", OperationStatus.COMPLETED, seq=2)
    _add_op(job, "FINISHING", OperationStatus.PENDING, seq=3)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.HEAT_TREATED


def test_never_moves_backwards_from_machined_to_blank():
    job = _job(JobType.FABRICATION, part_condition=PartCondition.MACHINED)
    _add_op(job, "BLANKING", OperationStatus.COMPLETED, seq=1)
    _add_op(job, "TURNING", OperationStatus.PENDING, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.MACHINED


def test_completed_history_checking_does_not_advance_stage():
    job = _job(JobType.FABRICATION)
    _add_op(job, "CHECKING", OperationStatus.COMPLETED)
    _add_op(job, "TURNING", OperationStatus.PENDING, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.RAW_MATERIAL


def test_repair_starts_as_client_supplied_item():
    job = _job(JobType.REPAIR)
    assert job.part_condition == PartCondition.CLIENT_SUPPLIED_ITEM
    _add_op(job, "TURNING", OperationStatus.COMPLETED)
    _add_op(job, "FINISHING", OperationStatus.PENDING, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.MACHINED


def test_job_page_shows_the_stage_from_completed_operations_even_if_never_saved():
    """Sample history completes operations without advancing the saved stage."""
    job = _job(JobType.FABRICATION)
    _add_op(job, "CUTTING", OperationStatus.COMPLETED)
    _add_op(job, "WELDING", OperationStatus.COMPLETED, seq=2)
    _add_op(job, "FINISHING", OperationStatus.PENDING, seq=3)
    db.session.refresh(job)
    assert job.part_condition == PartCondition.RAW_MATERIAL
    assert job.to_dict(include_operations=True)["partCondition"] == "ASSEMBLED"
    # List cards keep the saved stage, so they need no operation types loaded.
    assert job.to_dict()["partCondition"] == "RAW_MATERIAL"
    # Reading never changes the saved stage.
    assert job.part_condition == PartCondition.RAW_MATERIAL
