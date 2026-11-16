"""The delay Pareto as something to act on: incidents behind each bar, a
suggested action, the weekly trend, late jobs by cause, how much is still
"Other", and the Admin re-labelling an "Other" pause."""

from datetime import date

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models.job_order import JobOrderStatus
from app.models.operation_time import OperationPauseReason, OperationTimeEvent, OperationTimeLog
from app.models.user import UserRole
from app.services import analytics_service as svc
from app.services import pareto_insights
from tests.test_analytics_fixes import _finished_op, _job, _local, _user, shop  # noqa: F401

DAY = date(2026, 10, 5)


def _paused_job(shop, reason, note=None, due=None, delivered=None):
    job = _job(shop, JobOrderStatus.DELIVERED if delivered else JobOrderStatus.SCHEDULED)
    if due:
        job.due_date = due
    if delivered:
        job.delivered_at = _local(delivered, 15)
    _finished_op(
        shop,
        job,
        _local(DAY, 8),
        _local(DAY, 15),
        [
            (OperationTimeEvent.START, _local(DAY, 8), None),
            (OperationTimeEvent.PAUSE, _local(DAY, 9), reason),
            (OperationTimeEvent.RESUME, _local(DAY, 11), None),
            (OperationTimeEvent.COMPLETE, _local(DAY, 15), None),
        ],
    )
    if note:
        log = OperationTimeLog.query.filter_by(event=OperationTimeEvent.PAUSE).order_by(
            OperationTimeLog.created_at.desc()
        ).first()
        log.note = note
    db.session.commit()
    return job


def _delays():
    return svc.delays("2026-10-01", "2026-10-31")


def test_each_cause_lists_its_incidents_and_a_suggested_action(shop):
    _paused_job(shop, OperationPauseReason.WAITING_TOOL, note="Boring bar out")
    data = _delays()
    row = next(r for r in data["causes"] if r["cause"] == "WAITING_TOOL")
    assert row["label"] == "Waiting for a tool"
    assert row["incidentCount"] == 1
    inc = row["incidents"][0]
    assert inc["kind"] == "PAUSE" and inc["hours"] == 2.0
    assert inc["machine"] == "AF Lathe #1" and inc["note"] == "Boring bar out"
    assert inc["logId"] and inc["operation"] == "Turning"
    assert "AF Lathe #1" in row["action"]


def test_other_share_counts_other_hours_and_missing_notes(shop):
    _paused_job(shop, OperationPauseReason.OTHER)
    _paused_job(shop, OperationPauseReason.WAITING_INSTRUCTIONS)
    share = _delays()["otherShare"]
    assert share["hours"] == 2.0
    assert share["pct"] == 50.0
    assert share["withoutNote"] == 1


def test_weekly_trend_puts_hours_in_the_week_they_were_lost(shop):
    _paused_job(shop, OperationPauseReason.POWER_INTERRUPTION)
    trend = _delays()["weeklyTrend"]
    assert trend["weeks"][0] == "2026-09-28"
    series = next(s for s in trend["series"] if s["cause"] == "POWER_INTERRUPTION")
    week = trend["weeks"].index("2026-10-05")
    assert series["hours"][week] == 2.0
    assert sum(series["hours"]) == 2.0


def test_late_jobs_are_ranked_by_the_cause_that_cost_them_most(shop):
    _paused_job(shop, OperationPauseReason.MOVED_TO_OTHER_JOB, due=date(2026, 10, 6), delivered=date(2026, 10, 9))
    rows = _delays()["lateJobCauses"]
    assert rows[0]["cause"] == "MOVED_TO_OTHER_JOB"
    assert rows[0]["jobs"] == 1 and rows[0]["cumulativePct"] == 100.0


def _token(user):
    return {"Authorization": f"Bearer {create_access_token(identity=user.id, additional_claims={'role': user.role.value})}"}


def test_admin_relabels_an_other_pause_and_the_first_reason_is_kept(client, shop):
    _paused_job(shop, OperationPauseReason.OTHER)
    log = OperationTimeLog.query.filter_by(event=OperationTimeEvent.PAUSE).first()
    admin = _user("af_admin@test.local", UserRole.ADMIN)
    db.session.commit()

    res = client.patch(f"/api/v1/operations/time-logs/{log.id}/reason", json={"reason": "WAITING_TOOL"}, headers=_token(admin))
    assert res.status_code == 200
    body = res.get_json()
    assert body["reason"] == "WAITING_TOOL" and body["originalReason"] == "OTHER"

    worker = shop["worker"]
    refused = client.patch(f"/api/v1/operations/time-logs/{log.id}/reason", json={"reason": "OTHER"}, headers=_token(worker))
    assert refused.status_code == 403
    bad = client.patch(f"/api/v1/operations/time-logs/{log.id}/reason", json={"reason": "NAP"}, headers=_token(admin))
    assert bad.status_code == 400

    row = next(r for r in _delays()["causes"] if r["cause"] == "WAITING_TOOL")
    assert row["incidents"][0]["originalReason"] == "OTHER"


def test_machine_downtime_action_counts_one_stop_or_several():
    one = {"cause": "MACHINE_DOWNTIME", "details": [{"label": "Lathe #3", "hours": 3.0, "count": 1}]}
    two = {"cause": "MACHINE_DOWNTIME", "details": [{"label": "Lathe #3", "hours": 5.0, "count": 2}]}
    assert "(3.0 h over 1 stop)" in pareto_insights.suggested_action(one)
    assert "(5.0 h over 2 stops)" in pareto_insights.suggested_action(two)
