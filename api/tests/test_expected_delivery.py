"""Office Staff change an issued supplier order's expected delivery date.

Uses the bmsc_test database from conftest (schema built from the models).
"""

from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.job_order import JobOrder
from app.models.staff_alert import StaffAlert
from app.models.supplier_order import SupplierOrder, SupplierOrderStatus
from app.services import material_purchase_service as mp_service
from app.services import schedule_calendar as cal
from app.services.schedule_calendar import (
    SHOP_TZ,
    ensure_utc,
    next_shop_working_day,
    utc_to_shop,
)
from tests.catalog_helpers import approve_issue
from tests.test_material_delay import (  # noqa: F401  (fixtures)
    _draft_order,
    _headers,
    _issue,
    _job,
    _local,
    shop,
)


def _change(client, user, order_id, new_date, note="Supplier confirmed a new date"):
    return client.patch(
        f"/api/v1/supplier-orders/{order_id}/expected-delivery",
        json={
            "expectedDeliveryDate": new_date.isoformat() if new_date else None,
            "note": note,
        },
        headers=_headers(user),
    )


def _issued_for_job_in(client, shop, days_ahead, supplier="quick"):
    """A Scheduled job starting in ``days_ahead`` days, its material on an issued order."""
    job, ops = _job(shop, _local(shop["today"] + timedelta(days=days_ahead), 9))
    order = _draft_order(client, shop, job, shop[supplier])
    issued = _issue(client, shop, order["id"], shop["today"])
    return job, ops, issued


def _first_start(job):
    db.session.expire_all()
    job = db.session.get(JobOrder, job.id)
    return job, ensure_utc(min(job.operations, key=lambda o: o.sequence_no).scheduled_start)


def test_later_date_keeps_original_audits_note_and_moves_job(client, shop):
    job, ops, issued = _issued_for_job_in(client, shop, 5)
    _, before = _first_start(job)
    issued_expected = date.fromisoformat(issued["expectedDeliveryDate"])
    new = next_shop_working_day(shop["today"] + timedelta(days=8))
    note = "Supplier confirmed delivery on " + new.strftime("%d %b")

    res = _change(client, shop["office"], issued["id"], new, note)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["expectedDeliveryDate"] == new.isoformat()
    assert body["originalExpectedDeliveryDate"] == issued_expected.isoformat()
    assert body["expectedDeliveryNote"] == note
    assert [j["jobId"] for j in body["movedJobs"]] == [job.id]
    assert body["expectedDeliveryChanges"][0] == {
        "from": issued_expected.isoformat(),
        "to": new.isoformat(),
        "note": note,
        "changedByName": shop["office"].full_name,
        "changedAt": body["expectedDeliveryChanges"][0]["changedAt"],
    }

    job, after = _first_start(job)
    assert after > before
    assert utc_to_shop(after).date() == new
    assert "expected delivery changed" in job.material_delay_reason
    # The move alert; the earlier approval request is a different kind.
    assert (
        StaffAlert.query.filter(
            StaffAlert.recipient_id == shop["admin"].id, StaffAlert.kind != "PO_APPROVAL"
        ).count()
        == 1
    )

    entry = AuditLog.query.filter_by(
        entity_type="SupplierOrder", entity_id=issued["id"], action="EXPECTED_DELIVERY_CHANGED"
    ).one()
    assert entry.after_json == {"expectedDeliveryDate": new.isoformat(), "note": note}
    assert entry.before_json == {"expectedDeliveryDate": issued_expected.isoformat()}
    assert entry.user_id == shop["office"].id


def test_original_date_is_never_overwritten(client, shop):
    _, _, issued = _issued_for_job_in(client, shop, 20)
    original = issued["expectedDeliveryDate"]
    for days, note in ((6, "First push"), (9, "Second push")):
        res = _change(client, shop["office"], issued["id"], shop["today"] + timedelta(days=days), note)
        assert res.status_code == 200, res.get_json()
    body = client.get(
        f"/api/v1/supplier-orders/{issued['id']}", headers=_headers(shop["office"])
    ).get_json()
    assert body["originalExpectedDeliveryDate"] == original
    assert body["expectedDeliveryDate"] == next_shop_working_day(shop["today"] + timedelta(days=9)).isoformat()
    assert [c["note"] for c in body["expectedDeliveryChanges"]] == ["Second push", "First push"]


def test_earlier_date_moves_nothing(client, shop):
    job, _, issued = _issued_for_job_in(client, shop, 1, supplier="slow")
    job, moved_start = _first_start(job)
    assert job.material_delayed_at is not None
    alerts = StaffAlert.query.count()

    res = _change(client, shop["office"], issued["id"], shop["today"] + timedelta(days=4))
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["movedJobs"] == []
    _, start = _first_start(job)
    assert start == moved_start
    assert StaffAlert.query.count() == alerts


def test_change_applies_to_every_unreceived_line(client, shop):
    job_a, _ = _job(shop, _local(shop["today"] + timedelta(days=30), 9))
    job_b, _ = _job(shop, _local(shop["today"] + timedelta(days=30), 9))
    _draft_order(client, shop, job_a, shop["quick"])
    order = _draft_order(client, shop, job_b, shop["quick"])
    issued = _issue(client, shop, order["id"], shop["today"])
    so = db.session.get(SupplierOrder, issued["id"])
    # An order partly received before deliveries had to arrive complete.
    line_a = next(ln for ln in so.active_lines if ln.job_order_id == job_a.id)
    line_a.date_received = shop["today"]
    so.status = SupplierOrderStatus.PARTIALLY_RECEIVED
    db.session.commit()

    new = next_shop_working_day(shop["today"] + timedelta(days=12))
    assert _change(client, shop["office"], so.id, new).status_code == 200
    db.session.expire_all()
    so = db.session.get(SupplierOrder, issued["id"])
    arrivals = {
        ln.job_order_id: mp_service.line_expected_arrival(ln)["expectedArrival"]
        for ln in so.active_lines
    }
    assert arrivals[job_a.id] == shop["today"].isoformat()
    assert arrivals[job_b.id] == new.isoformat()


def test_only_office_staff_can_change_it(client, shop):
    _, _, issued = _issued_for_job_in(client, shop, 20)
    new = shop["today"] + timedelta(days=5)
    assert _change(client, shop["admin"], issued["id"], new).status_code == 403
    assert _change(client, shop["worker"], issued["id"], new).status_code == 403
    so = db.session.get(SupplierOrder, issued["id"])
    assert so.original_expected_delivery_date is None


def test_note_and_date_are_validated(client, shop):
    _, _, issued = _issued_for_job_in(client, shop, 20)
    oid, office = issued["id"], shop["office"]
    assert _change(client, office, oid, shop["today"] + timedelta(days=5), "  ").status_code == 400
    assert _change(client, office, oid, None).status_code == 400
    res = _change(client, office, oid, shop["today"] - timedelta(days=1))
    assert res.status_code == 400 and "issue date" in res.get_json()["error"]["message"]
    same = date.fromisoformat(issued["expectedDeliveryDate"])
    assert _change(client, office, oid, same).status_code == 400


def test_received_cancelled_and_draft_orders_cannot_be_changed(client, shop):
    new = shop["today"] + timedelta(days=5)

    _, _, received = _issued_for_job_in(client, shop, 20)
    so = db.session.get(SupplierOrder, received["id"])
    client.post(f"/api/v1/supplier-orders/{so.id}/print", headers=_headers(shop["office"]))
    client.post(
        f"/api/v1/supplier-orders/{so.id}/receive",
        json={"receivedDate": shop["today"].isoformat()},
        headers=_headers(shop["office"]),
    )
    assert _change(client, shop["office"], so.id, new).status_code == 409

    _, _, cancelled = _issued_for_job_in(client, shop, 20)
    client.post(
        f"/api/v1/supplier-orders/{cancelled['id']}/cancel", json={}, headers=_headers(shop["office"])
    )
    assert _change(client, shop["office"], cancelled["id"], new).status_code == 409

    job, _ = _job(shop, _local(shop["today"] + timedelta(days=20), 9))
    draft = _draft_order(client, shop, job, shop["slow"])
    assert _change(client, shop["office"], draft["id"], new).status_code == 409


def test_issue_without_date_uses_manila_day_at_utc_sunday(client, shop, monkeypatch):
    """Monday 00:30 PHT is still Sunday in UTC — default issue date must be Monday."""
    from app.services import supplier_order_service as so_service

    monday_0030_pht = datetime(2026, 10, 19, 0, 30, tzinfo=SHOP_TZ)
    assert monday_0030_pht.astimezone(timezone.utc).date() == date(2026, 10, 18)
    # issue_order binds shop_now at import time — patch both the calendar
    # module and the supplier-order service's copy.
    monkeypatch.setattr(cal, "shop_now", lambda: monday_0030_pht)
    monkeypatch.setattr(so_service, "shop_now", lambda: monday_0030_pht)

    job, _ = _job(shop, _local(shop["today"] + timedelta(days=20), 9))
    order = _draft_order(client, shop, job, shop["quick"])
    res = approve_issue(client, f"/api/v1/supplier-orders/{order['id']}", {}, _headers(shop["office"]))
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["dateIssued"] == "2026-10-19"
