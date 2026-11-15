"""Start rules: one running operation per worker and per machine unit; any
crew member starts and completes; starting ahead of the schedule is allowed."""

from datetime import timedelta

from app.extensions import db

from tests.test_crew import MON, _at, _headers, _job, _op, shop  # noqa: F401


def _start(client, op, user):
    return client.post(f"/api/v1/operations/{op.id}/start", json={}, headers=_headers(user))


def test_worker_cannot_run_two_operations_at_once(client, shop):
    ana = shop["ana"]
    first = _op(_job(shop), 1, shop["types"]["LAYOUT"], lead=ana)
    second = _op(_job(shop), 1, shop["types"]["LAYOUT"], lead=ana)
    assert _start(client, first, ana).status_code == 200

    res = _start(client, second, ana)
    assert res.status_code == 409
    body = res.get_json()
    assert body["error"]["code"] == "WORKER_BUSY"
    assert "You're still on Layout" in body["error"]["message"]
    assert "Pause or finish it first" in body["error"]["message"]

    # Pausing the first frees the worker; resuming it while the second runs is refused.
    url = f"/api/v1/operations/{first.id}"
    assert client.post(f"{url}/pause", json={"reason": "BREAK"}, headers=_headers(ana)).status_code == 200
    assert _start(client, second, ana).status_code == 200
    res = client.post(f"{url}/resume", json={}, headers=_headers(ana))
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "WORKER_BUSY"


def test_machine_unit_runs_one_operation_even_when_other_units_are_free(client, shop):
    ana, ben = shop["ana"], shop["ben"]
    turning = shop["types"]["TURNING"]
    first = _op(_job(shop), 1, turning, lead=ana)
    second = _op(_job(shop), 1, turning, lead=ben)
    other_unit = _op(_job(shop), 1, turning, lead=shop["cara"])
    first.machine_unit_id = shop["lathe2"].id
    second.machine_unit_id = shop["lathe2"].id
    other_unit.machine_unit_id = shop["lathe8"].id
    db.session.commit()

    assert _start(client, first, ana).status_code == 200
    res = _start(client, second, ben)
    assert res.status_code == 409
    body = res.get_json()
    assert body["error"]["code"] == "MACHINE_UNIT_BUSY"
    assert "Lathe #2 is running Turning" in body["error"]["message"]
    # The other lathe is free, so work booked there still starts.
    assert _start(client, other_unit, shop["cara"]).status_code == 200


def test_helper_can_start_and_complete_and_early_start_is_allowed(client, shop):
    ana, ben = shop["ana"], shop["ben"]
    next_week = MON + timedelta(days=7)
    op = _op(
        _job(shop), 1, shop["types"]["LAYOUT"], lead=ana, helpers=[ben],
        start=_at(next_week, 8), end=_at(next_week, 10),
    )
    # The helper starts it today, a week ahead of its booking, and completes it.
    assert _start(client, op, ben).status_code == 200
    res = client.post(f"/api/v1/operations/{op.id}/complete", json={}, headers=_headers(ben))
    assert res.status_code == 200
    assert res.get_json()["status"] == "COMPLETED"
