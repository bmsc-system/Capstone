"""Process-flow rules: material consumption, start gate, part stages, invoice before delivery.

Runs against the test database (TEST_DATABASE_URL) inside a rolled-back session.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import create_app
from app.config import Config
from app.extensions import bcrypt, db
from app.models.client import Client
from app.models.job_order import (
    JobOrder,
    JobOrderStatus,
    JobType,
    MaterialStatus,
    PartCondition,
)
from app.models.material_purchase import MaterialPurchase
from app.models.operation import JobOperation, OperationStatus
from app.models.supplier import Supplier
from app.models.user import User, UserRole, UserStatus
from app.models.worker_skill import OperationType
from app.services import material_purchase_service as mp_service
from app.services import operation_service
from app.services import sales_invoice_service as si_service
from app.services.schedule_calendar import shop_now
from app.services.job_order_service import (
    _PART_CONDITION_RANK,
    advance_part_condition,
    create_job_order,
    mark_job_delivered,
    mark_job_for_delivery,
    update_job_order,
)
from app.utils.errors import AppError


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
        monkeypatch.setattr(
            "app.services.notification_service.safe_notify_job_milestone",
            lambda *a, **k: None,
        )
        try:
            yield
        finally:
            db.session.rollback()
            db.session.remove()


def _user(email, role):
    u = User(
        email=email,
        password_hash=bcrypt.generate_password_hash("Pass123!").decode("utf-8"),
        full_name=email.split("@")[0],
        role=role,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(u)
    db.session.flush()
    return u


def _ensure_op_type(code: str) -> OperationType:
    existing = OperationType.query.filter_by(code=code).first()
    if existing:
        return existing
    ot = OperationType(code=code, name=code.replace("_", " ").title(), active=True)
    db.session.add(ot)
    db.session.flush()
    return ot


@pytest.fixture
def people():
    return {
        "office": _user("pf_office@test.local", UserRole.OFFICE_STAFF),
        "worker": _user("pf_worker@test.local", UserRole.PRODUCTION_WORKER),
        "admin": _user("pf_admin@test.local", UserRole.ADMIN),
    }


def _job(people, material_status=MaterialStatus.ORDERED, status=JobOrderStatus.SCHEDULED,
         amount=Decimal("15000.00"), part_condition=PartCondition.RAW_MATERIAL):
    client = Client(name="PF Test Client")
    db.session.add(client)
    db.session.flush()
    job = JobOrder(
        client_id=client.id,
        title="Process Flow Job",
        description="Fabricate 10 pcs shaft",
        due_date=date(2026, 12, 1),
        status=status,
        job_type=JobType.FABRICATION,
        part_condition=part_condition,
        material_status=material_status,
        amount=amount,
        created_by_id=people["office"].id,
    )
    db.session.add(job)
    db.session.flush()
    return job


def _add_op(job, code, worker_id=None, status=OperationStatus.SCHEDULED, seq=1):
    ot = _ensure_op_type(code)
    op = JobOperation(
        job_order_id=job.id,
        sequence_no=seq,
        operation_name=ot.name,
        operation_type_id=ot.id,
        assigned_worker_id=worker_id,
        status=status,
        estimated_hours=Decimal("1"),
    )
    db.session.add(op)
    db.session.flush()
    op.operation_type = ot
    return op


def _line(job, name, received=None, qty="2", cost="500"):
    supplier = Supplier.query.filter_by(name="PF Supplier").first()
    if not supplier:
        supplier = Supplier(name="PF Supplier", active=True)
        db.session.add(supplier)
        db.session.flush()
    line = MaterialPurchase(
        job_order_id=job.id,
        material_name=name,
        quantity=Decimal(qty),
        unit="pcs",
        unit_cost=Decimal(cost),
        supplier_id=supplier.id,
        date_ordered=date(2026, 9, 1),
        date_received=received,
    )
    db.session.add(line)
    db.session.flush()
    return line


# --- Consumption trigger ---


def test_first_operation_start_consumes_every_received_line(people):
    job = _job(people)
    a = _line(job, "Round bar S45C", received=date(2026, 9, 3))
    b = _line(job, "Hex nut M20", received=date(2026, 9, 4))
    op1 = _add_op(job, "TURNING", people["worker"].id, seq=1)
    _add_op(job, "FINISHING", people["worker"].id, seq=2)
    db.session.refresh(job)
    assert a.status == "RECEIVED" and b.status == "RECEIVED"

    ts = datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)
    operation_service.start_operation(
        op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value, ts.isoformat()
    )

    assert a.consumed_at == ts and b.consumed_at == ts
    assert a.status == "CONSUMED" and a.to_dict()["status"] == "CONSUMED"


def test_later_operation_start_does_not_touch_consumed_at(people):
    job = _job(people)
    line = _line(job, "Plate A36", received=date(2026, 9, 3))
    op1 = _add_op(job, "CUTTING", people["worker"].id, seq=1)
    op2 = _add_op(job, "WELDING", people["worker"].id, seq=2)
    db.session.refresh(job)
    t1 = datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)
    operation_service.start_operation(
        op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value, t1.isoformat()
    )
    operation_service.complete_operation(
        op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value,
        datetime(2026, 9, 10, 2, 0, tzinfo=timezone.utc).isoformat(),
    )
    operation_service.start_operation(
        op2, people["worker"].id, UserRole.PRODUCTION_WORKER.value,
        datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc).isoformat(),
    )
    assert line.consumed_at == t1


def test_line_received_after_job_started_is_consumed_immediately(people):
    job = _job(people, material_status=MaterialStatus.RECEIVED)
    op1 = _add_op(job, "TURNING", people["worker"].id)
    op1.actual_start = datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)
    late = _line(job, "Extra shim", received=None)
    db.session.refresh(job)
    mp_service.receive_lines([late], "2026-09-12")
    assert late.status == "CONSUMED"


def test_consumed_line_cannot_have_received_date_cleared(people):
    job = _job(people)
    line = _line(job, "Round bar", received=date(2026, 9, 3))
    line.consumed_at = datetime(2026, 9, 10, tzinfo=timezone.utc)
    with pytest.raises(AppError):
        mp_service.update_purchase(line, {"dateReceived": None})


# --- Start gate ---


def test_start_gate_blocks_first_operation_while_any_line_ordered(people):
    job = _job(people)
    received = _line(job, "Round bar S45C", received=date(2026, 9, 3))
    _line(job, "Bronze bushing", received=None)
    op1 = _add_op(job, "TURNING", people["worker"].id)
    db.session.refresh(job)

    with pytest.raises(AppError) as exc:
        operation_service.start_operation(
            op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value, None
        )
    assert exc.value.code == "MATERIALS_NOT_RECEIVED"
    assert "have not arrived" in exc.value.message
    assert "Bronze bushing" not in exc.value.message
    assert op1.actual_start is None
    assert received.consumed_at is None


def test_start_gate_lists_outstanding_lines_for_admin(people):
    job = _job(people)
    _line(job, "Bronze bushing", received=None)
    op1 = _add_op(job, "TURNING", people["admin"].id)
    db.session.refresh(job)
    with pytest.raises(AppError) as exc:
        operation_service.start_operation(
            op1, people["admin"].id, UserRole.ADMIN.value, None
        )
    assert "Bronze bushing" in exc.value.message


def test_start_gate_passes_once_all_lines_received(people):
    job = _job(people)
    pending = _line(job, "Bronze bushing", received=None)
    op1 = _add_op(job, "TURNING", people["worker"].id)
    db.session.refresh(job)
    mp_service.receive_lines([pending], "2026-09-05")
    operation_service.start_operation(
        op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value, None
    )
    assert op1.status == OperationStatus.IN_PROGRESS
    assert pending.status == "CONSUMED"


def test_start_gate_blocks_to_order_job_with_no_purchase_lines(people):
    job = _job(people, material_status=MaterialStatus.TO_ORDER)
    op1 = _add_op(job, "TURNING", people["worker"].id)
    db.session.refresh(job)

    with pytest.raises(AppError) as exc:
        operation_service.start_operation(
            op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value, None
        )
    assert exc.value.code == "MATERIALS_NOT_ORDERED"
    assert exc.value.message == (
        "The materials for this job have not been ordered yet. "
        "Please check with the office."
    )
    assert op1.actual_start is None


def test_start_gate_to_order_message_names_job_for_admin(people):
    job = _job(people, material_status=MaterialStatus.TO_ORDER)
    op1 = _add_op(job, "TURNING", people["admin"].id)
    db.session.refresh(job)
    with pytest.raises(AppError) as exc:
        operation_service.start_operation(
            op1, people["admin"].id, UserRole.ADMIN.value, None
        )
    assert exc.value.code == "MATERIALS_NOT_ORDERED"
    assert job.job_number in exc.value.message
    assert "No material has been ordered" in exc.value.message


def test_to_order_job_starts_once_set_not_required(people):
    job = _job(people, material_status=MaterialStatus.TO_ORDER)
    op1 = _add_op(job, "TURNING", people["worker"].id)
    db.session.refresh(job)
    job.material_status = MaterialStatus.NOT_REQUIRED
    operation_service.start_operation(
        op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value, None
    )
    assert op1.status == OperationStatus.IN_PROGRESS


def test_start_gate_passes_when_every_line_received(people):
    job = _job(people, material_status=MaterialStatus.RECEIVED)
    a = _line(job, "Round bar", received=date(2026, 9, 3))
    b = _line(job, "Hex nut", received=date(2026, 9, 4))
    op1 = _add_op(job, "TURNING", people["worker"].id)
    db.session.refresh(job)
    operation_service.start_operation(
        op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value, None
    )
    assert op1.status == OperationStatus.IN_PROGRESS
    assert a.status == "CONSUMED" and b.status == "CONSUMED"


@pytest.mark.parametrize("status", [MaterialStatus.ORDERED, MaterialStatus.RECEIVED])
def test_start_gate_blocks_ordered_or_received_job_with_no_lines(people, status):
    job = _job(people, material_status=status)
    op1 = _add_op(job, "TURNING", people["worker"].id)
    db.session.refresh(job)
    with pytest.raises(AppError) as exc:
        operation_service.start_operation(
            op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value, None
        )
    assert exc.value.code == "MATERIALS_NOT_ORDERED"
    assert "not been ordered" in exc.value.message
    assert op1.actual_start is None


def test_update_refuses_received_without_purchase_lines(people):
    job = _job(people, material_status=MaterialStatus.TO_ORDER, status=JobOrderStatus.DRAFT)
    db.session.refresh(job)
    with pytest.raises(AppError) as exc:
        update_job_order(job, {"materialStatus": "RECEIVED"}, actor_role=UserRole.OFFICE_STAFF.value)
    assert exc.value.code == "VALIDATION_ERROR"
    assert "TO_ORDER or NOT_REQUIRED" in exc.value.message


def test_update_refuses_ordered_without_purchase_lines(people):
    job = _job(people, material_status=MaterialStatus.TO_ORDER, status=JobOrderStatus.DRAFT)
    db.session.refresh(job)
    with pytest.raises(AppError) as exc:
        update_job_order(job, {"materialStatus": "ORDERED"}, actor_role=UserRole.OFFICE_STAFF.value)
    assert exc.value.code == "VALIDATION_ERROR"


def test_create_refuses_received_material_status(people):
    client = Client(name="PF Create Client")
    db.session.add(client)
    db.session.flush()
    with pytest.raises(AppError) as exc:
        create_job_order(
            {
                "clientId": client.id,
                "title": "New job",
                "dueDate": "2026-12-01",
                "materialStatus": "RECEIVED",
            },
            people["office"].id,
        )
    assert exc.value.code == "VALIDATION_ERROR"


def test_update_with_lines_keeps_status_derived(people):
    job = _job(people, material_status=MaterialStatus.ORDERED, status=JobOrderStatus.DRAFT)
    _line(job, "Round bar", received=None)
    db.session.refresh(job)
    update_job_order(job, {"materialStatus": "RECEIVED"}, actor_role=UserRole.OFFICE_STAFF.value)
    assert job.material_status == MaterialStatus.ORDERED


def test_office_cannot_set_not_required_on_released_job(people):
    job = _job(people, material_status=MaterialStatus.TO_ORDER)
    _add_op(job, "TURNING", people["worker"].id)
    db.session.refresh(job)
    with pytest.raises(AppError) as exc:
        update_job_order(
            job, {"materialStatus": "NOT_REQUIRED"}, actor_role=UserRole.OFFICE_STAFF.value
        )
    assert exc.value.code == "JOB_INFO_LOCKED"
    assert "locked after release" in exc.value.message.lower()


def test_admin_can_set_not_required_on_released_job(people):
    job = _job(people, material_status=MaterialStatus.TO_ORDER)
    _add_op(job, "TURNING", people["worker"].id)
    db.session.refresh(job)
    update_job_order(job, {"materialStatus": "NOT_REQUIRED"}, actor_role=UserRole.ADMIN.value)
    assert job.material_status == MaterialStatus.NOT_REQUIRED


def test_office_can_choose_not_required_while_pending(people):
    job = _job(people, material_status=MaterialStatus.TO_ORDER, status=JobOrderStatus.DRAFT)
    update_job_order(
        job, {"materialStatus": "NOT_REQUIRED"}, actor_role=UserRole.OFFICE_STAFF.value
    )
    assert job.material_status == MaterialStatus.NOT_REQUIRED


def test_start_gate_ignores_not_required_jobs(people):
    job = _job(people, material_status=MaterialStatus.NOT_REQUIRED)
    _line(job, "Stray line", received=None)
    op1 = _add_op(job, "TURNING", people["worker"].id)
    db.session.refresh(job)
    operation_service.start_operation(
        op1, people["worker"].id, UserRole.PRODUCTION_WORKER.value, None
    )
    assert op1.status == OperationStatus.IN_PROGRESS


# --- Part condition order ---


def test_part_condition_rank_order_including_wip():
    order = [
        PartCondition.WORK_IN_PROCESS,
        PartCondition.CUT,
        PartCondition.BLANK,
        PartCondition.FORMED,
        PartCondition.MACHINED,
        PartCondition.ASSEMBLED,
        PartCondition.HEAT_TREATED,
        PartCondition.FINISHED,
    ]
    ranks = [_PART_CONDITION_RANK[p] for p in order]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    assert _PART_CONDITION_RANK[PartCondition.RAW_MATERIAL] < ranks[0]
    assert (
        _PART_CONDITION_RANK[PartCondition.CLIENT_SUPPLIED_ITEM]
        == _PART_CONDITION_RANK[PartCondition.RAW_MATERIAL]
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        ("CUTTING", PartCondition.CUT),
        ("BLANKING", PartCondition.BLANK),
        ("BENDING", PartCondition.FORMED),
        ("FORMING", PartCondition.FORMED),
        ("TURNING", PartCondition.MACHINED),
        ("WELDING", PartCondition.ASSEMBLED),
        ("ASSEMBLY", PartCondition.ASSEMBLED),
        ("HEAT_TREATMENT", PartCondition.HEAT_TREATED),
        ("FINISHING", PartCondition.FINISHED),
        ("DEBURRING", PartCondition.WORK_IN_PROCESS),
    ],
)
def test_completed_op_maps_to_stage(people, code, expected):
    job = _job(people, status=JobOrderStatus.IN_PROGRESS)
    _add_op(job, code, status=OperationStatus.COMPLETED, seq=1)
    _add_op(job, "FINISHING", status=OperationStatus.PENDING, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == expected


def test_finishing_never_demoted_and_all_complete_still_finished(people):
    job = _job(people, status=JobOrderStatus.IN_PROGRESS)
    _add_op(job, "FINISHING", status=OperationStatus.COMPLETED, seq=1)
    _add_op(job, "CUTTING", status=OperationStatus.COMPLETED, seq=2)
    _add_op(job, "FINISHING", status=OperationStatus.PENDING, seq=3)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.FINISHED

    job2 = _job(people, status=JobOrderStatus.IN_PROGRESS)
    _add_op(job2, "CUTTING", status=OperationStatus.COMPLETED, seq=1)
    _add_op(job2, "TURNING", status=OperationStatus.COMPLETED, seq=2)
    db.session.refresh(job2)
    advance_part_condition(job2)
    assert job2.part_condition == PartCondition.FINISHED


def test_completed_history_checking_does_not_change_stage(people):
    job = _job(people, status=JobOrderStatus.IN_PROGRESS, part_condition=PartCondition.CUT)
    _add_op(job, "CHECKING", status=OperationStatus.COMPLETED, seq=1)
    _add_op(job, "TURNING", status=OperationStatus.PENDING, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.CUT


def test_wip_never_overwrites_a_later_stage(people):
    job = _job(people, status=JobOrderStatus.IN_PROGRESS, part_condition=PartCondition.FORMED)
    _add_op(job, "DEBURRING", status=OperationStatus.COMPLETED, seq=1)
    _add_op(job, "CUTTING", status=OperationStatus.COMPLETED, seq=2)
    _add_op(job, "FINISHING", status=OperationStatus.PENDING, seq=3)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.FORMED


def test_specific_stage_overwrites_wip(people):
    job = _job(
        people, status=JobOrderStatus.IN_PROGRESS,
        part_condition=PartCondition.WORK_IN_PROCESS,
    )
    _add_op(job, "CUTTING", status=OperationStatus.COMPLETED, seq=1)
    _add_op(job, "FINISHING", status=OperationStatus.PENDING, seq=2)
    db.session.refresh(job)
    advance_part_condition(job)
    assert job.part_condition == PartCondition.CUT


# --- Invoice before delivery ---


def _completed_job(people, **kw):
    job = _job(people, status=JobOrderStatus.COMPLETED, **kw)
    _add_op(job, "TURNING", status=OperationStatus.COMPLETED)
    db.session.refresh(job)
    return job


def _invoice_data(job, **kw):
    """The office copies the number from the shop's BIR-registered invoice."""
    return {
        "invoiceNumber": f"PF-TEST-{job.id}",
        "invoiceDate": shop_now().date().isoformat(),
        **kw,
    }


def test_delivery_blocked_until_invoice_recorded(people):
    job = _completed_job(people)
    with pytest.raises(AppError) as exc:
        mark_job_for_delivery(job)
    assert exc.value.code == "INVOICE_REQUIRED"
    assert job.status == JobOrderStatus.COMPLETED

    si_service.record_invoice(job, _invoice_data(job), people["office"].id)
    mark_job_for_delivery(job)
    assert job.status == JobOrderStatus.FOR_DELIVERY
    assert job.for_delivery_at is not None
    assert job.delivered_at is None

    mark_job_delivered(job)
    assert job.status == JobOrderStatus.DELIVERED
    assert job.delivered_at is not None


def test_delivered_needs_for_delivery_first(people):
    job = _completed_job(people)
    si_service.record_invoice(job, _invoice_data(job), people["office"].id)
    with pytest.raises(AppError) as exc:
        mark_job_delivered(job)
    assert exc.value.code == "INVALID_TRANSITION"
    assert job.status == JobOrderStatus.COMPLETED
    assert job.delivered_at is None


@pytest.mark.parametrize("status", [JobOrderStatus.SCHEDULED, JobOrderStatus.IN_PROGRESS])
def test_for_delivery_needs_a_completed_job(people, status):
    job = _job(people, status=status)
    _add_op(job, "TURNING", status=OperationStatus.COMPLETED)
    db.session.refresh(job)
    with pytest.raises(AppError) as exc:
        mark_job_for_delivery(job)
    assert exc.value.code == "INVALID_TRANSITION"
    assert "Completed" in exc.value.message
    assert job.status == status


def test_invoice_requires_completed_job(people):
    job = _job(people, status=JobOrderStatus.IN_PROGRESS)
    with pytest.raises(AppError) as exc:
        si_service.record_invoice(job, _invoice_data(job), people["office"].id)
    assert exc.value.code == "INVALID_TRANSITION"


def test_invoice_amount_defaults_from_job_and_can_be_entered(people):
    job = _completed_job(people)
    inv = si_service.record_invoice(job, _invoice_data(job), people["office"].id)
    assert inv.total == Decimal("15000.00")
    assert inv.client_id == job.client_id
    assert inv.invoice_number == f"PF-TEST-{job.id}"
    assert inv.invoice_seq is None

    job2 = _completed_job(people)
    inv2 = si_service.record_invoice(job2, _invoice_data(job2, amount="11200"), people["office"].id)
    assert inv2.total == Decimal("11200.00")
    assert job.amount == Decimal("15000.00")


def test_invoice_recorded_only_once(people):
    job = _completed_job(people)
    si_service.record_invoice(job, _invoice_data(job), people["office"].id)
    with pytest.raises(AppError) as exc:
        si_service.record_invoice(job, _invoice_data(job, invoiceNumber="PF-OTHER"), people["office"].id)
    assert exc.value.code == "INVOICE_EXISTS"


def test_completing_an_operation_records_the_hours_worked(people):
    job = _job(people)
    _line(job, "Round bar S45C", received=date(2026, 9, 3))
    op = _add_op(job, "TURNING", people["worker"].id, seq=1)
    op.estimated_hours = Decimal("2")
    db.session.refresh(job)
    worker = people["worker"].id
    role = UserRole.PRODUCTION_WORKER.value
    operation_service.start_operation(
        op, worker, role, datetime(2026, 9, 10, 0, 30, tzinfo=timezone.utc).isoformat()
    )
    operation_service.complete_operation(
        op, worker, role, datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc).isoformat()
    )
    assert op.actual_worked_hours == Decimal("2.5000")
    assert op.variance_hours == Decimal("0.5000")
