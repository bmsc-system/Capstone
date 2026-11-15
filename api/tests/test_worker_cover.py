"""What-ifs on the shop floor: a worker absent, an emergency in the middle of
an operation, a hand-over by the Administrator, and an early finish."""

from datetime import timedelta

from app.extensions import db
from app.models.operation import JobOperation, OperationStatus
from app.models.schedule_move import ScheduleMove
from app.models.staff_alert import StaffAlert
from app.models.worker_absence import WorkerAbsence
from app.services import worker_cover_service as cover

from tests.test_crew import MON, _at, _headers, _job, _op, _suggest, shop  # noqa: F401


def _away(client, shop, worker, day=MON, reason="ABSENT", to=None):
    return client.post(
        "/api/v1/attendance/absences",
        json={
            "workerId": worker.id,
            "fromDate": day.isoformat(),
            "toDate": (to or day).isoformat(),
            "reason": reason,
        },
        headers=_headers(shop["admin"]),
    )


def _turning(shop, lead, start_h=8, end_h=10, day=MON, unit="lathe2"):
    op = _op(_job(shop), 1, shop["types"]["TURNING"], lead=lead,
             start=_at(day, start_h), end=_at(day, end_h))
    op.machine_unit_id = shop[unit].id
    db.session.commit()
    return op


def test_absent_worker_work_goes_to_the_best_free_replacement(client, shop):
    op = _turning(shop, shop["ana"])
    res = _away(client, shop, shop["ana"])
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    row = body["cover"]["operations"][0]
    assert row["outcome"] == "REASSIGNED"

    db.session.refresh(op)
    # Someone skilled on the lathe took it at the same booked time.
    assert op.assigned_worker_id in (shop["ben"].id, shop["gio"].id)
    assert op.scheduled_start == _at(MON, 8) and op.scheduled_end == _at(MON, 10)
    assert shop["ana"].id not in op.crew_ids
    assert "given to" in body["summary"]
    titles = [a.title for a in StaffAlert.query.filter_by(recipient_id=shop["admin"].id)]
    assert any("Ana Crew: absent" in t for t in titles)
    # Only the Administrator marks people away.
    assert client.post("/api/v1/attendance/absences", json={"workerId": shop["ana"].id},
                       headers=_headers(shop["office"])).status_code == 403


def test_when_nobody_is_free_the_work_moves_to_after_the_absence(client, shop):
    op = _turning(shop, shop["ana"])
    # The other lathe hands are away that day too.
    for name in ("ben", "gio"):
        db.session.add(WorkerAbsence(worker_id=shop[name].id, starts_at=_at(MON, 0),
                                     ends_at=_at(MON, 23), reason="LEAVE"))
    db.session.commit()

    res = _away(client, shop, shop["ana"])
    assert res.status_code == 201, res.get_json()
    row = res.get_json()["cover"]["operations"][0]
    assert row["outcome"] == "MOVED_LATER"
    db.session.refresh(op)
    assert op.assigned_worker_id == shop["ana"].id
    assert op.scheduled_start >= _at(MON + timedelta(days=1), 8)
    move = ScheduleMove.query.filter_by(job_order_id=op.job_order_id).one()
    assert move.kind == "WORKER_ABSENT"


def test_away_worker_is_not_suggested_or_booked(client, shop):
    assert _away(client, shop, shop["gio"]).status_code == 201
    names = [
        s["fullName"]
        for s in _suggest(
            client, shop, shop["types"]["TURNING"],
            scheduledStart=_at(MON, 8).isoformat(), scheduledEnd=_at(MON, 10).isoformat(),
        )["suggestions"]
    ]
    assert "Gio Crew" not in names and "Ben Crew" in names
    # Back early: the period is cancelled and the worker is offered again.
    absence = WorkerAbsence.query.filter_by(worker_id=shop["gio"].id).one()
    res = client.post(f"/api/v1/attendance/absences/{absence.id}/cancel", json={},
                      headers=_headers(shop["admin"]))
    assert res.status_code == 200 and res.get_json()["cancelledAt"]
    names = [
        s["fullName"]
        for s in _suggest(
            client, shop, shop["types"]["TURNING"],
            scheduledStart=_at(MON, 8).isoformat(), scheduledEnd=_at(MON, 10).isoformat(),
        )["suggestions"]
    ]
    assert "Gio Crew" in names


def test_emergency_pause_hands_the_running_work_to_the_helper(client, shop):
    ana, ben = shop["ana"], shop["ben"]
    op = _op(_job(shop), 1, shop["types"]["LAYOUT"], lead=ana, helpers=[ben])
    later = _op(_job(shop), 1, shop["types"]["LAYOUT"], lead=ana,
                start=_at(MON, 8), end=_at(MON, 10))
    url = f"/api/v1/operations/{op.id}"
    assert client.post(f"{url}/start", json={}, headers=_headers(ana)).status_code == 200
    res = client.post(f"{url}/pause", json={"reason": "EMERGENCY"}, headers=_headers(ana))
    assert res.status_code == 200, res.get_json()

    db.session.refresh(op)
    assert op.status == OperationStatus.IN_PROGRESS
    assert op.assigned_worker_id == ben.id and ana.id not in op.crew_ids
    # Away for the rest of today only; next week's work stays with the worker.
    absence = WorkerAbsence.query.filter_by(worker_id=ana.id).one()
    assert absence.reason == "EMERGENCY"
    db.session.refresh(later)
    assert later.assigned_worker_id == ana.id
    # The helper resumes and finishes it; the worker who left cannot.
    assert client.post(f"{url}/resume", json={}, headers=_headers(ana)).status_code == 403
    assert client.post(f"{url}/resume", json={}, headers=_headers(ben)).status_code == 200
    assert client.post(f"{url}/complete", json={}, headers=_headers(ben)).status_code == 200


def test_admin_hands_over_a_paused_operation_and_the_new_worker_resumes(client, shop):
    ana, cara = shop["ana"], shop["cara"]
    op = _op(_job(shop), 1, shop["types"]["LAYOUT"], lead=ana)
    url = f"/api/v1/operations/{op.id}"
    assert client.post(f"{url}/start", json={}, headers=_headers(ana)).status_code == 200
    admin = _headers(shop["admin"])
    # Still running: it has to be paused first.
    assert client.post(f"{url}/hand-over", json={"workerId": cara.id}, headers=admin).status_code == 409
    assert client.post(f"{url}/pause", json={"reason": "OTHER"}, headers=_headers(ana)).status_code == 200

    options = client.get(f"{url}/replacements", headers=admin).get_json()
    assert cara.id in [c["workerId"] for c in options["candidates"]]
    res = client.post(f"{url}/hand-over", json={"workerId": cara.id}, headers=admin)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["to"] == "Cara Crew" and res.get_json()["from"] == "Ana Crew"
    assert client.post(f"{url}/resume", json={}, headers=_headers(cara)).status_code == 200
    # Only the Administrator hands work over.
    assert client.post(f"{url}/hand-over", json={}, headers=_headers(shop["office"])).status_code == 403


def test_hand_over_refuses_a_worker_who_is_booked_then(client, shop):
    op = _turning(shop, shop["ana"])
    _turning(shop, shop["ben"], unit="lathe8")  # Ben is on another job at that time
    res = client.post(f"/api/v1/operations/{op.id}/hand-over", json={"workerId": shop["ben"].id},
                      headers=_headers(shop["admin"]))
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "WORKER_NOT_AVAILABLE"
    # With nobody named, the best free worker is picked.
    res = client.post(f"/api/v1/operations/{op.id}/hand-over", json={}, headers=_headers(shop["admin"]))
    assert res.status_code == 200 and res.get_json()["to"] == "Gio Crew"


def test_early_finish_pulls_the_next_step_forward(client, shop):
    ana, ben = shop["ana"], shop["ben"]
    job = _job(shop)
    first = _op(job, 1, shop["types"]["LAYOUT"], lead=ana, start=_at(MON, 8), end=_at(MON, 10))
    second = _op(job, 2, shop["types"]["LAYOUT"], lead=ben, start=_at(MON, 10), end=_at(MON, 12))
    url = f"/api/v1/operations/{first.id}"
    # Done today, days ahead of the booking.
    assert client.post(f"{url}/start", json={}, headers=_headers(ana)).status_code == 200
    assert client.post(f"{url}/complete", json={}, headers=_headers(ana)).status_code == 200

    db.session.refresh(second)
    assert second.scheduled_start < _at(MON, 10)
    assert second.assigned_worker_id == ben.id
    move = ScheduleMove.query.filter_by(job_order_id=job.id).one()
    assert move.kind == "EARLY_FINISH" and move.new_start < move.previous_start
    # It is not a delay: the job carries no delay reason.
    db.session.refresh(job)
    assert job.material_delay_reason is None and job.delay_kind is None
    titles = [a.title for a in StaffAlert.query.filter_by(recipient_id=shop["admin"].id)]
    assert any("moved earlier" in t for t in titles)


def test_pull_forward_leaves_a_step_that_cannot_start_sooner(app, shop):
    ana, ben = shop["ana"], shop["ben"]
    job = _job(shop)
    _op(job, 1, shop["types"]["LAYOUT"], lead=ana, status=OperationStatus.COMPLETED)
    second = _op(job, 2, shop["types"]["LAYOUT"], lead=ben, start=_at(MON, 8), end=_at(MON, 10))
    # Ben is away until the booked day, so nothing earlier is free.
    from app.services.schedule_calendar import shop_now, shop_local_to_utc
    from datetime import time

    db.session.add(WorkerAbsence(
        worker_id=ben.id, starts_at=shop_local_to_utc(shop_now().date(), time(0, 0)),
        ends_at=_at(MON, 7), reason="LEAVE",
    ))
    db.session.commit()
    assert cover.pull_forward(JobOperation.query.get(second.id).job_order) is None
    db.session.refresh(second)
    assert second.scheduled_start == _at(MON, 8)


def test_leave_entered_ahead_keeps_running_work_and_covers_the_booked_days(client, shop):
    ana = shop["ana"]
    running = _op(_job(shop), 1, shop["types"]["LAYOUT"], lead=ana)
    booked = _turning(shop, ana)
    assert client.post(f"/api/v1/operations/{running.id}/start", json={}, headers=_headers(ana)).status_code == 200

    res = _away(client, shop, ana, reason="LEAVE")  # next Monday only
    assert res.status_code == 201, res.get_json()
    done = {r["operationId"]: r["outcome"] for r in res.get_json()["cover"]["operations"]}
    assert done == {booked.id: "REASSIGNED"}
    db.session.refresh(running)
    assert running.assigned_worker_id == ana.id
    # Still theirs to pause and finish today.
    assert client.post(f"/api/v1/operations/{running.id}/complete", json={}, headers=_headers(ana)).status_code == 200
