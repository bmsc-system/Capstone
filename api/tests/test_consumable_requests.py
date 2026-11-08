"""Running-low reports from the shop floor, and what workers may see of inventory."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.consumable_request import ConsumableRequest, ConsumableRequestStatus
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus
from app.models.material_purchase import MaterialPurchase
from app.models.operation import JobOperation, OperationStatus
from app.models.staff_alert import StaffAlert
from app.models.tool import Tool, ToolCategory
from app.models.user import UserRole
from app.services.schedule_calendar import shop_now
from tests.test_material_delay import _headers, _user

BASE = "/api/v1/consumables"


@pytest.fixture
def shop(app):
    office = _user("cr_office@test.local", UserRole.OFFICE_STAFF)
    admin = _user("cr_admin@test.local", UserRole.ADMIN)
    worker = _user("cr_worker@test.local", UserRole.PRODUCTION_WORKER)
    other = _user("cr_other@test.local", UserRole.PRODUCTION_WORKER)
    disc = Tool(code="CR-DISC", name="Cutting disc", category=ToolCategory.CONSUMABLE, unit="pcs",
                size_spec='4"', unit_cost=Decimal("65"))
    db.session.add(disc)
    db.session.commit()
    return {"office": office, "admin": admin, "worker": worker, "other": other, "disc": disc}


def _report(client, shop, level="LOW", who="worker"):
    return client.post(f"{BASE}/{shop['disc'].id}/report", json={"level": level}, headers=_headers(shop[who]))


def test_worker_reports_low_and_office_is_told(client, shop):
    res = _report(client, shop)
    assert res.status_code == 201
    body = res.get_json()
    assert body["level"] == "LOW" and body["status"] == "OPEN" and body["mine"] is True

    alerts = StaffAlert.query.filter_by(recipient_id=shop["office"].id).all()
    assert [a.title for a in alerts] == ["Cutting disc is running low"]
    # The Admin's bell is not used for this; the office buys consumables.
    assert StaffAlert.query.filter_by(recipient_id=shop["admin"].id).count() == 0

    listed = client.get(f"{BASE}/requests", headers=_headers(shop["office"])).get_json()
    assert [r["name"] for r in listed] == ["Cutting disc"]


def test_repeat_taps_keep_one_report_and_out_raises_the_level(client, shop):
    _report(client, shop)
    _report(client, shop)
    _report(client, shop, who="other")
    assert ConsumableRequest.query.count() == 1
    assert StaffAlert.query.filter_by(recipient_id=shop["office"].id).count() == 1

    res = _report(client, shop, level="OUT", who="other")
    assert res.get_json()["level"] == "OUT"
    assert ConsumableRequest.query.count() == 1
    titles = sorted(a.title for a in StaffAlert.query.filter_by(recipient_id=shop["office"].id))
    assert titles == ["Cutting disc is out", "Cutting disc is running low"]
    # Out again, or Running low after Out, changes nothing.
    _report(client, shop, level="OUT")
    assert _report(client, shop, level="LOW").get_json()["level"] == "OUT"
    assert StaffAlert.query.filter_by(recipient_id=shop["office"].id).count() == 2


def test_only_workers_report_and_only_office_dismisses(client, shop):
    assert _report(client, shop, who="office").status_code == 403
    assert _report(client, shop, who="admin").status_code == 403
    rid = _report(client, shop).get_json()["id"]
    assert client.post(f"{BASE}/requests/{rid}/dismiss", headers=_headers(shop["worker"])).status_code == 403
    assert client.post(f"{BASE}/requests/{rid}/dismiss", headers=_headers(shop["admin"])).status_code == 403
    res = client.post(f"{BASE}/requests/{rid}/dismiss", headers=_headers(shop["office"]))
    assert res.get_json()["status"] == "DISMISSED"
    assert client.get(f"{BASE}/requests", headers=_headers(shop["office"])).get_json() == []


def test_only_the_reporter_can_take_it_back(client, shop):
    rid = _report(client, shop).get_json()["id"]
    assert client.post(f"{BASE}/requests/{rid}/withdraw", headers=_headers(shop["other"])).status_code == 403
    res = client.post(f"{BASE}/requests/{rid}/withdraw", headers=_headers(shop["worker"]))
    assert res.get_json()["status"] == "WITHDRAWN"
    # A new tap after taking it back opens a fresh report.
    assert _report(client, shop).status_code == 201
    assert ConsumableRequest.query.filter_by(status=ConsumableRequestStatus.OPEN).count() == 1


def test_recording_a_purchase_closes_the_report(client, shop):
    rid = _report(client, shop).get_json()["id"]
    body = {"toolId": shop["disc"].id, "purchasedOn": shop_now().date().isoformat(), "quantity": 10, "unitCost": 65}
    res = client.post(f"{BASE}/purchases", json=body, headers=_headers(shop["office"]))
    assert res.status_code == 201
    req = db.session.get(ConsumableRequest, rid)
    assert req.status == ConsumableRequestStatus.BOUGHT
    assert req.consumable_purchase_id == res.get_json()["id"]
    assert req.closed_by_id == shop["office"].id


def test_receiving_a_supplier_order_line_closes_the_report(app, shop):
    from app.models.supplier import Supplier
    from app.services.material_purchase_service import _receive_consumable

    rid = None
    with app.test_request_context():
        from app.services import consumable_request_service as svc

        rid = svc.report(shop["disc"].id, "OUT", shop["worker"].id).id
        sup = Supplier(name="CR Supplier", code="CRS")
        db.session.add(sup)
        db.session.flush()
        line = MaterialPurchase(
            tool_id=shop["disc"].id, supplier_id=sup.id, material_name="Cutting disc", quantity=Decimal("20"),
            unit="pcs", date_ordered=shop_now().date(),
        )
        db.session.add(line)
        db.session.flush()
        _receive_consumable(line, shop_now().date(), shop["office"].id)
        db.session.commit()
    req = db.session.get(ConsumableRequest, rid)
    assert req.status == ConsumableRequestStatus.BOUGHT and req.material_purchase_id is not None


def test_worker_list_shows_reports_first_without_prices(client, shop):
    db.session.add(Tool(code="CR-ROD", name="Arc rod", category=ToolCategory.CONSUMABLE, unit="kg"))
    db.session.add(Tool(code="CR-OLD", name="Old item", category=ToolCategory.CONSUMABLE, unit="pcs", active=False))
    db.session.commit()
    _report(client, shop, who="other")
    assert client.get(f"{BASE}/for-worker", headers=_headers(shop["office"])).status_code == 403
    rows = client.get(f"{BASE}/for-worker", headers=_headers(shop["worker"])).get_json()
    assert [r["name"] for r in rows] == ["Cutting disc", "Arc rod"]
    assert rows[0]["report"]["level"] == "LOW" and rows[0]["report"]["mine"] is False
    assert rows[0]["sizeSpec"] == '4"'
    assert all("unitCost" not in r and "lastUnitCost" not in r for r in rows)


def _job_with_material(worker, status=OperationStatus.SCHEDULED, received=False):
    from app.models.client import Client

    from app.models.supplier import Supplier

    client = Client.query.filter_by(name="CR Client").first()
    supplier = Supplier.query.filter_by(code="CRM").first()
    if client is None:
        client = Client(name="CR Client")
        supplier = Supplier(name="CR Metals", code="CRM")
        db.session.add_all([client, supplier])
        db.session.flush()
    job = JobOrder(
        client_id=client.id,
        created_by_id=worker.id,
        title="Shaft", job_type=JobType.MACHINING, status=JobOrderStatus.SCHEDULED,
        due_date=date.today() + timedelta(days=10), quantity=1, material_status=MaterialStatus.ORDERED,
    )
    db.session.add(job)
    db.session.flush()
    db.session.add(JobOperation(job_order_id=job.id, sequence_no=1, operation_name="Turning",
                                assigned_worker_id=worker.id, status=status, estimated_hours=2))
    db.session.add(MaterialPurchase(
        job_order_id=job.id, supplier_id=supplier.id, material_name="1045", size_spec="320mm dia x 170mmL", quantity=Decimal("1"),
        unit="pcs", unit_cost=Decimal("4200"), date_ordered=date.today(),
        date_received=date.today() if received else None,
    ))
    db.session.commit()
    return job


def test_worker_sees_materials_of_own_open_jobs_only(client, shop):
    mine = _job_with_material(shop["worker"], received=True)
    _job_with_material(shop["other"])
    _job_with_material(shop["worker"], status=OperationStatus.COMPLETED)
    rows = client.get("/api/v1/operations/mine/materials", headers=_headers(shop["worker"])).get_json()
    assert [r["jobId"] for r in rows] == [mine.id]
    line = rows[0]["lines"][0]
    assert line["materialName"] == "1045" and line["sizeSpec"] == "320mm dia x 170mmL"
    assert line["status"] == "RECEIVED"
    assert not any(k in line for k in ("unitCost", "amount", "supplierName", "poNumber"))
    assert client.get("/api/v1/operations/mine/materials", headers=_headers(shop["office"])).status_code == 403
