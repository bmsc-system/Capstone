"""Header bell: staff alerts, and the material delay alert to the Admin.

Uses the bmsc_test database from conftest (schema built from the models).
"""

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.staff_alert import StaffAlert, StaffAlertKind
from app.models.supplier_order import SupplierOrder
from app.models.user import UserRole
from app.services import material_delay_service as delay_service
from app.services import staff_alert_service
from tests.test_material_delay import (  # noqa: F401  (fixtures)
    _draft_order,
    _headers,
    _issue,
    _job,
    _local,
    _user,
    shop,
)


def _alerts(user):
    """The user's alerts, leaving out the request to approve a supplier order."""
    return StaffAlert.query.filter(
        StaffAlert.recipient_id == user.id, StaffAlert.kind != "PO_APPROVAL"
    ).all()


def test_material_delay_creates_one_alert_per_admin(client, shop):
    second_admin = _user("md_admin2@test.local", UserRole.ADMIN)
    db.session.commit()
    tomorrow = shop["today"] + timedelta(days=1)
    job, _ = _job(shop, _local(tomorrow, 9))
    order = _draft_order(client, shop, job, shop["slow"])
    issued = _issue(client, shop, order["id"], shop["today"])

    db.session.expire_all()
    job = db.session.get(type(job), job.id)
    for admin in (shop["admin"], second_admin):
        alerts = _alerts(admin)
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.kind == StaffAlertKind.MATERIAL_DELAY
        assert alert.message == job.material_delay_reason
        assert alert.job_order_id == job.id
        assert alert.supplier_order_id == order["id"]
        assert job.job_number in alert.title
    assert _alerts(shop["office"]) == []

    body = client.get("/api/v1/alerts", headers=_headers(shop["admin"])).get_json()
    # The delay alert, plus the earlier request to approve the order.
    assert body["unreadCount"] == 2
    delay = next(i for i in body["items"] if i["kind"] == StaffAlertKind.MATERIAL_DELAY)
    assert delay["poNumber"] == issued["poNumber"]
    assert delay["jobNumber"] == job.job_number


def test_each_delay_is_its_own_alert_and_no_move_means_no_alert(client, shop):
    tomorrow = shop["today"] + timedelta(days=1)
    job, _ = _job(shop, _local(tomorrow, 9))
    order = _draft_order(client, shop, job, shop["slow"])
    _issue(client, shop, order["id"], shop["today"])
    assert len(_alerts(shop["admin"])) == 1

    so = db.session.get(SupplierOrder, order["id"])
    so.expected_delivery_date = so.expected_delivery_date + timedelta(days=2)
    db.session.commit()
    delay_service.reschedule_for_order(so, delay_service.EXPECTED_DATE_CHANGED)
    assert len(_alerts(shop["admin"])) == 2

    so.expected_delivery_date = shop["today"] + timedelta(days=1)
    db.session.commit()
    delay_service.reschedule_for_order(so, delay_service.EXPECTED_DATE_CHANGED)
    assert len(_alerts(shop["admin"])) == 2


def test_material_in_time_raises_no_alert(client, shop):
    later = shop["today"] + timedelta(days=5)
    job, _ = _job(shop, _local(later, 9))
    order = _draft_order(client, shop, job, shop["quick"])
    _issue(client, shop, order["id"], shop["today"])
    assert _alerts(shop["admin"]) == []


def _seed(user, title, minutes_ago):
    alert = StaffAlert(
        recipient_id=user.id,
        kind="TEST",
        title=title,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    db.session.add(alert)
    db.session.commit()
    return alert


def test_list_is_newest_first_and_read_state_is_per_user(client, shop):
    old = _seed(shop["admin"], "Older", 30)
    _seed(shop["admin"], "Newer", 5)
    office_alert = _seed(shop["office"], "Office one", 1)
    h = _headers(shop["admin"])

    body = client.get("/api/v1/alerts", headers=h).get_json()
    assert [a["title"] for a in body["items"]] == ["Newer", "Older"]
    assert body["unreadCount"] == 2

    res = client.post(f"/api/v1/alerts/{old.id}/read", headers=h)
    assert res.status_code == 200 and res.get_json()["read"] is True
    assert client.get("/api/v1/alerts/unread-count", headers=h).get_json() == {"unreadCount": 1}

    assert client.post(f"/api/v1/alerts/{office_alert.id}/read", headers=h).status_code == 404

    assert client.post("/api/v1/alerts/read-all", headers=h).get_json() == {"marked": 1}
    assert client.get("/api/v1/alerts/unread-count", headers=h).get_json() == {"unreadCount": 0}
    office = client.get("/api/v1/alerts/unread-count", headers=_headers(shop["office"]))
    assert office.get_json() == {"unreadCount": 1}


def test_workers_cannot_use_the_bell(client, shop):
    h = _headers(shop["worker"])
    assert client.get("/api/v1/alerts", headers=h).status_code == 403
    assert client.get("/api/v1/alerts/unread-count", headers=h).status_code == 403
    assert client.post("/api/v1/alerts/read-all", headers=h).status_code == 403


def test_dedupe_key_raises_an_alert_once_per_recipient(app, shop):
    for _ in range(2):
        staff_alert_service.raise_alert(
            roles=[UserRole.ADMIN, UserRole.OFFICE_STAFF],
            kind="OVERDUE_DELIVERY",
            title="PO overdue",
            dedupe_key="overdue:po-1:2026-10-03",
        )
        db.session.commit()
    assert len(_alerts(shop["admin"])) == 1
    assert len(_alerts(shop["office"])) == 1
