"""Audit fixes: delivered sales, Pareto non-working pauses, worked-hours efficiency,
operation rebuild lock, and server-side role gaps.

Runs in the rolled-back test database session from test_process_flow. Analytics cases
use a far-future period (2031) so other rows don't interfere.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models.job_order import JobOrder, JobOrderStatus
from app.models.machine import MachineType, MachineUnit
from app.models.operation import JobOperation, OperationStatus
from app.models.operation_time import (
    DowntimeCategory,
    MachineDowntime,
    OperationPauseReason,
    OperationTimeEvent,
    OperationTimeLog,
)
from app.models.user import UserRole
from app.services import analytics_service, forecast_service
from app.services.job_order_service import cancel_job_order, update_job_order
from app.services.scoring_service import _pairs_from_ops, score_efficiency
from app.utils.errors import AppError
from tests.test_process_flow import (  # noqa: F401  (fixtures)
    _add_op,
    _job,
    _rollback_txn,
    _user,
    app,
    people,
)

PERIOD = {"from_s": "2031-03-01", "to_s": "2031-03-31"}
T0 = datetime(2031, 3, 10, 0, 0, tzinfo=timezone.utc)


def _finished_job(people, status, amount, end_at):
    job = _job(people, status=status, amount=Decimal(amount))
    op = _add_op(job, "TURNING", people["worker"].id, status=OperationStatus.COMPLETED)
    op.actual_start = end_at - timedelta(hours=2)
    op.actual_end = end_at
    op.actual_worked_hours = Decimal("2")
    db.session.flush()
    return job, op


def _headers(user):
    token = create_access_token(identity=user.id, additional_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


# 1. Sales include DELIVERED jobs


def test_sales_summary_and_forecast_include_delivered_jobs(people):
    # The forecast drops the first (partial) month of history, so March needs an earlier month.
    _finished_job(people, JobOrderStatus.COMPLETED, "0", datetime(2031, 1, 15, tzinfo=timezone.utc))
    _finished_job(people, JobOrderStatus.DELIVERED, "5000", T0)
    _finished_job(people, JobOrderStatus.COMPLETED, "3000", T0 + timedelta(days=1))

    summary = analytics_service.sales_summary(**PERIOD)
    assert summary["completedJobCount"] == 2
    assert summary["totalAmount"] == pytest.approx(8000.0)

    monthly = forecast_service.sales_forecast(today=date(2031, 4, 15))
    march = next(m for m in monthly["months"] if m["month"] == "2031-03")
    assert march["actual"] == pytest.approx(8000.0)

    forecast = analytics_service.sales_forecast(**PERIOD)
    open_jobs = JobOrder.query.filter(
        JobOrder.status.in_((JobOrderStatus.SCHEDULED, JobOrderStatus.IN_PROGRESS))
    ).count()
    assert forecast["committedPipeline"]["jobCount"] == open_jobs


# 2. Pareto excludes breaks and end-of-shift


def test_pareto_excludes_break_and_end_of_shift(people):
    job, op = _finished_job(people, JobOrderStatus.COMPLETED, "1000", T0 + timedelta(hours=40))
    worker_id = people["worker"].id
    events = [
        (OperationTimeEvent.START, 0, None),
        (OperationTimeEvent.PAUSE, 1, OperationPauseReason.BREAK),
        (OperationTimeEvent.RESUME, 2, None),
        (OperationTimeEvent.PAUSE, 3, OperationPauseReason.END_OF_SHIFT),
        (OperationTimeEvent.RESUME, 17, None),
        (OperationTimeEvent.PAUSE, 18, OperationPauseReason.WAITING_MATERIAL),
        (OperationTimeEvent.RESUME, 20, None),
        (OperationTimeEvent.COMPLETE, 21, None),
    ]
    for event, hour, reason in events:
        db.session.add(
            OperationTimeLog(
                operation_id=op.id,
                worker_id=worker_id,
                event=event,
                event_at=T0 + timedelta(hours=hour),
                reason=reason,
            )
        )
    db.session.flush()

    data = analytics_service.delays(**PERIOD)
    causes = {row["cause"]: row for row in data["causes"]}
    assert "BREAK" not in causes
    assert "END_OF_SHIFT" not in causes
    assert causes["WAITING_MATERIAL"]["hours"] == pytest.approx(2.0)
    assert data["causes"][-1]["cumulativePct"] == pytest.approx(100.0)
    assert sum(r["shareOfTotalPct"] for r in data["causes"]) == pytest.approx(100.0, abs=0.01)

    excluded = data["excludedNonWorkingPauses"]
    assert excluded["breakHours"] == pytest.approx(1.0)
    assert excluded["endOfShiftHours"] == pytest.approx(14.0)
    assert excluded["totalHours"] == pytest.approx(15.0)
    # The pause log itself still lists every pause.
    assert {r["reason"] for r in data["pauseReasons"]} >= {"BREAK", "END_OF_SHIFT"}


# 3. Worker efficiency uses worked hours


def test_efficiency_pairs_use_worked_hours_not_clock_time():
    start = datetime(2031, 3, 10, 0, 0, tzinfo=timezone.utc)
    overnight = SimpleNamespace(
        actual_start=start,
        actual_end=start + timedelta(hours=20),
        actual_worked_hours=Decimal("4"),
        estimated_hours=Decimal("4"),
    )
    no_hours = SimpleNamespace(
        actual_start=start,
        actual_end=start + timedelta(hours=3),
        actual_worked_hours=None,
        estimated_hours=Decimal("3"),
    )
    assert _pairs_from_ops([overnight, no_hours]) == [(4.0, 4.0)]

    score, _, used_default = score_efficiency([(4.0, 4.0), (2.0, 2.0)])
    assert used_default is False
    assert score == pytest.approx(1.0 / 1.5)


# 4. No operation rebuild after release


def test_released_job_refuses_operation_replacement(people):
    job = _job(people, status=JobOrderStatus.SCHEDULED)
    _add_op(job, "TURNING", people["worker"].id, status=OperationStatus.COMPLETED)
    db.session.refresh(job)
    with pytest.raises(AppError) as exc:
        update_job_order(
            job,
            {"operations": [{"operationName": "Turning", "estimatedHours": 1}]},
            actor_role=UserRole.ADMIN.value,
        )
    assert exc.value.code == "OPERATIONS_LOCKED"
    assert "pending" in exc.value.message


def test_draft_job_can_still_replace_operations(people):
    job = _job(people, status=JobOrderStatus.DRAFT)
    _add_op(job, "TURNING", people["worker"].id, status=OperationStatus.PENDING)
    db.session.refresh(job)
    update_job_order(
        job,
        {"operations": [{"operationName": "Facing", "estimatedHours": 2}]},
        actor_role=UserRole.ADMIN.value,
    )
    names = [o.operation_name for o in JobOperation.query.filter_by(job_order_id=job.id)]
    assert names == ["Facing"]


def test_cancel_keeps_started_operations_and_blocks_completed(people):
    job = _job(people, status=JobOrderStatus.IN_PROGRESS)
    op = _add_op(job, "TURNING", people["worker"].id, status=OperationStatus.IN_PROGRESS)
    op.actual_start = T0
    db.session.flush()
    db.session.refresh(job)
    cancel_job_order(job, people["admin"].id, {"reason": "CLIENT_CANCELLED"})
    db.session.refresh(job)
    db.session.refresh(op)
    assert job.status == JobOrderStatus.CANCELLED
    assert op.actual_start is not None
    assert JobOperation.query.get(op.id) is not None

    done = _job(people, status=JobOrderStatus.COMPLETED)
    db.session.flush()
    with pytest.raises(AppError) as exc:
        cancel_job_order(done, people["admin"].id, {"reason": "CLIENT_CANCELLED"})
    assert exc.value.code == "INVALID_TRANSITION"


# 5. Server-side role gaps


def test_assign_is_admin_only(app, people):
    job = _job(people, status=JobOrderStatus.SCHEDULED)
    op = _add_op(job, "TURNING", people["worker"].id, status=OperationStatus.SCHEDULED)
    res = app.test_client().patch(
        f"/api/v1/operations/{op.id}/assign",
        json={"assignedWorkerId": people["worker"].id},
        headers=_headers(people["office"]),
    )
    assert res.status_code == 403


def test_admin_can_assign_operation_not_yet_started(app, people):
    other = _user("pf_worker2@test.local", UserRole.PRODUCTION_WORKER)
    job = _job(people, status=JobOrderStatus.SCHEDULED)
    op = _add_op(job, "TURNING", people["worker"].id, status=OperationStatus.SCHEDULED)
    res = app.test_client().patch(
        f"/api/v1/operations/{op.id}/assign",
        json={"assignedWorkerId": other.id},
        headers=_headers(people["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["assignedWorkerId"] == other.id


def test_admin_cannot_reassign_started_operation(app, people):
    other = _user("pf_worker2@test.local", UserRole.PRODUCTION_WORKER)
    job = _job(people, status=JobOrderStatus.IN_PROGRESS)
    op = _add_op(job, "TURNING", people["worker"].id, status=OperationStatus.IN_PROGRESS)
    op.actual_start = T0
    db.session.flush()
    res = app.test_client().patch(
        f"/api/v1/operations/{op.id}/assign",
        json={"assignedWorkerId": other.id},
        headers=_headers(people["admin"]),
    )
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "OPERATION_STARTED"


def test_security_headers_on_api_responses(app):
    res = app.test_client().get("/api/v1/clients")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_production_refuses_to_start_without_secrets():
    from app import create_app
    from app.config import DEV_JWT_SECRET_KEY, DEV_SECRET_KEY, Config

    class ProdNoSecrets(Config):
        ENV = "production"
        TESTING = False
        SECRET_KEY = DEV_SECRET_KEY
        JWT_SECRET_KEY = DEV_JWT_SECRET_KEY

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(ProdNoSecrets)


def _open_downtime(reporter):
    unit = MachineUnit.query.filter_by(active=True).first()
    if unit is None:
        machine_type = MachineType(code="PF_LATHE", name="Test lathe")
        db.session.add(machine_type)
        db.session.flush()
        unit = MachineUnit(machine_type_id=machine_type.id, label="Test lathe 1")
        db.session.add(unit)
        db.session.flush()
    row = MachineDowntime(
        machine_unit_id=unit.id,
        started_at=T0,
        category=DowntimeCategory.MECHANICAL_FAILURE,
        reason="Mechanical failure",
        reported_by_id=reporter.id,
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_other_worker_cannot_close_downtime(app, people):
    other = _user("pf_worker2@test.local", UserRole.PRODUCTION_WORKER)
    row = _open_downtime(people["worker"])
    res = app.test_client().post(
        f"/api/v1/operations/machine-units/downtime/{row.id}/close",
        json={},
        headers=_headers(other),
    )
    assert res.status_code == 403
    assert db.session.get(MachineDowntime, row.id).ended_at is None


@pytest.mark.parametrize("closer", ["worker", "office"])
def test_only_the_admin_puts_a_machine_back_in_service(app, people, closer):
    row = _open_downtime(people["worker"])
    res = app.test_client().post(
        f"/api/v1/operations/machine-units/downtime/{row.id}/close",
        json={},
        headers=_headers(people[closer]),
    )
    assert res.status_code == 403
    assert db.session.get(MachineDowntime, row.id).ended_at is None


def test_admin_can_close_downtime(app, people):
    row = _open_downtime(people["worker"])
    res = app.test_client().post(
        f"/api/v1/operations/machine-units/downtime/{row.id}/close",
        json={},
        headers=_headers(people["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["endedAt"] is not None


# Pareto detail: each cause says where its hours came from


def test_pareto_causes_name_the_machine_and_jobs(people):
    job, op = _finished_job(people, JobOrderStatus.COMPLETED, "1000", T0 + timedelta(hours=10))
    machine_type = MachineType(code="PD_MILL", name="Detail mill")
    db.session.add(machine_type)
    db.session.flush()
    unit = MachineUnit(machine_type_id=machine_type.id, label="Detail mill 1")
    db.session.add(unit)
    db.session.flush()
    op.machine_unit_id = unit.id
    for event, hour, reason in [
        (OperationTimeEvent.START, 0, None),
        (OperationTimeEvent.PAUSE, 2, OperationPauseReason.WAITING_MATERIAL),
        (OperationTimeEvent.RESUME, 4, None),
        (OperationTimeEvent.COMPLETE, 10, None),
    ]:
        db.session.add(
            OperationTimeLog(operation_id=op.id, worker_id=people["worker"].id, event=event,
                             event_at=T0 + timedelta(hours=hour), reason=reason)
        )
    db.session.add(
        MachineDowntime(machine_unit_id=unit.id, started_at=T0 + timedelta(days=1),
                        ended_at=T0 + timedelta(days=1, hours=3), category=DowntimeCategory.MECHANICAL_FAILURE,
                        reason="Spindle", reported_by_id=people["worker"].id)
    )
    db.session.flush()

    causes = {row["cause"]: row for row in analytics_service.delays(**PERIOD)["causes"]}
    waiting = causes["WAITING_MATERIAL"]
    assert waiting["detailBy"] == "machine"
    assert waiting["details"][0]["label"] == "Detail mill 1"
    assert waiting["details"][0]["hours"] == pytest.approx(2.0)
    assert waiting["details"][0]["jobs"] == [job.job_number]
    down = causes["MACHINE_DOWNTIME"]
    assert [d["label"] for d in down["details"]] == ["Detail mill 1"]
    assert down["details"][0]["count"] == 1
