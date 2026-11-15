"""Consumables kept by the Office Staff: hand-outs to workers for an operation,
stock on hand, what workers may see, and the usage-based replenishment forecast."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus
from app.models.material_purchase import MaterialPurchase
from app.models.operation import JobOperation, OperationStatus
from app.models.supplier import Supplier
from app.models.tool import Tool, ToolCategory
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.user import UserRole
from app.services import replenishment_service
from app.services.schedule_calendar import shop_local_to_utc, shop_now
from tests.test_material_delay import _headers, _user

BASE = "/api/v1/consumables"


@pytest.fixture
def shop(app):
    office = _user("ci_office@test.local", UserRole.OFFICE_STAFF)
    admin = _user("ci_admin@test.local", UserRole.ADMIN)
    worker = _user("ci_worker@test.local", UserRole.PRODUCTION_WORKER)
    other = _user("ci_other@test.local", UserRole.PRODUCTION_WORKER)
    disc = Tool(code="CI-DISC", name="Cutting disc", category=ToolCategory.CONSUMABLE, unit="pcs",
                size_spec='4"', unit_cost=Decimal("65"), quantity_on_hand=Decimal("10"))
    db.session.add(disc)
    db.session.commit()
    return {"office": office, "admin": admin, "worker": worker, "other": other, "disc": disc}


def _job(worker, status=OperationStatus.SCHEDULED):
    client = Client.query.filter_by(name="CI Client").first()
    if client is None:
        client = Client(name="CI Client")
        db.session.add(client)
        db.session.flush()
    job = JobOrder(
        client_id=client.id, created_by_id=worker.id, title="Shaft", job_type=JobType.MACHINING,
        status=JobOrderStatus.SCHEDULED, due_date=date.today() + timedelta(days=10), quantity=1,
        material_status=MaterialStatus.NOT_REQUIRED,
    )
    db.session.add(job)
    db.session.flush()
    op = JobOperation(job_order_id=job.id, sequence_no=1, operation_name="Turning",
                      assigned_worker_id=worker.id, status=status, estimated_hours=2)
    db.session.add(op)
    db.session.commit()
    return job, op


def _issue(client, shop, who="office", **body):
    data = {"toolId": shop["disc"].id, "workerId": shop["worker"].id, "quantity": 2, **body}
    return client.post(f"{BASE}/issues", json=data, headers=_headers(shop[who]))


def test_office_hands_a_consumable_to_the_operation_the_worker_is_on(client, shop):
    job, op = _job(shop["worker"])
    assert client.post(f"/api/v1/operations/{op.id}/start", json={}, headers=_headers(shop["worker"])).status_code == 200

    # The worker's options: their open operations, the running one picked.
    opts = client.get(f"{BASE}/issue-options", query_string={"workerId": shop["worker"].id},
                      headers=_headers(shop["office"])).get_json()
    assert opts["currentOperationId"] == op.id
    workers = client.get(f"{BASE}/issue-workers", headers=_headers(shop["office"])).get_json()
    assert workers[0]["workerId"] == shop["worker"].id
    assert workers[0]["currentOperation"].startswith("Turning (")

    res = _issue(client, shop)  # no operation given: goes on the running one
    assert res.status_code == 201, res.get_json()
    row = res.get_json()
    assert row["operationId"] == op.id and row["jobOrderId"] == job.id
    assert row["quantity"] == 2 and row["onHandAfter"] == 8
    assert row["issuedByName"] == shop["office"].full_name
    db.session.refresh(shop["disc"])
    assert shop["disc"].quantity_on_hand == Decimal("8")
    event = db.session.get(ToolEvent, row["id"])
    assert event.type == ToolEventType.ISSUE and event.stock_delta == Decimal("-2")


def test_only_office_hands_out_and_never_more_than_is_on_hand(client, shop):
    assert _issue(client, shop, who="admin").status_code == 403
    assert _issue(client, shop, who="worker").status_code == 403
    res = _issue(client, shop, quantity=11)
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert "Only 10 pcs" in res.get_json()["error"]["message"]
    assert _issue(client, shop, quantity=0).status_code == 400
    # An operation the worker is not on is refused.
    _job_other, op_other = _job(shop["other"])
    assert _issue(client, shop, operationId=op_other.id).status_code == 400
    db.session.refresh(shop["disc"])
    assert shop["disc"].quantity_on_hand == Decimal("10")


def test_undo_returns_the_quantity_to_stock(client, shop):
    row = _issue(client, shop, operationId=None).get_json()
    assert row["operationId"] is None
    assert client.post(f"{BASE}/issues/{row['id']}/undo", json={}, headers=_headers(shop["worker"])).status_code == 403
    res = client.post(f"{BASE}/issues/{row['id']}/undo", json={}, headers=_headers(shop["office"]))
    assert res.status_code == 200 and res.get_json()["onHandAfter"] == 10
    assert res.get_json()["undoneAt"]
    again = client.post(f"{BASE}/issues/{row['id']}/undo", json={}, headers=_headers(shop["office"]))
    assert again.status_code == 400
    listed = client.get(f"{BASE}/issues", headers=_headers(shop["admin"])).get_json()
    assert [r["id"] for r in listed] == [row["id"]]


def test_worker_sees_what_is_available_and_what_they_were_given(client, shop):
    db.session.add(Tool(code="CI-OLD", name="Old item", category=ToolCategory.CONSUMABLE, unit="pcs", active=False))
    db.session.commit()
    job, op = _job(shop["worker"])
    _issue(client, shop, operationId=op.id, quantity=3)
    _issue(client, shop, workerId=shop["other"].id, operationId=None, quantity=1)

    assert client.get(f"{BASE}/for-worker", headers=_headers(shop["office"])).status_code == 403
    view = client.get(f"{BASE}/for-worker", headers=_headers(shop["worker"])).get_json()
    assert [(i["name"], i["onHand"]) for i in view["items"]] == [("Cutting disc", 6)]
    assert all("unitCost" not in i and "lastUnitCost" not in i for i in view["items"])
    assert [(u["quantity"], u["operationName"]) for u in view["used"]] == [(3, "Turning")]

    # Used on that operation: the job's crew sees it; a worker not on the job does not.
    used = client.get(f"{BASE}/job/{job.id}/issues", headers=_headers(shop["worker"])).get_json()
    assert [(u["operationId"], u["quantity"]) for u in used] == [(op.id, 3)]
    assert client.get(f"{BASE}/job/{job.id}/issues", headers=_headers(shop["other"])).status_code == 403
    # Workers can no longer report an item as running low.
    res = client.post(f"{BASE}/{shop['disc'].id}/report", json={"level": "LOW"}, headers=_headers(shop["worker"]))
    assert res.status_code in (404, 405)


def test_recording_and_removing_a_purchase_moves_stock(client, shop):
    res = client.post(f"{BASE}/purchases", json={"toolId": shop["disc"].id, "quantity": 20, "unitCost": 60},
                      headers=_headers(shop["office"]))
    assert res.status_code == 201, res.get_json()
    db.session.refresh(shop["disc"])
    assert shop["disc"].quantity_on_hand == Decimal("30")
    assert client.delete(f"{BASE}/purchases/{res.get_json()['id']}", headers=_headers(shop["office"])).status_code == 200
    db.session.refresh(shop["disc"])
    assert shop["disc"].quantity_on_hand == Decimal("10")


def _hand_out_on(shop, day, qty):
    db.session.add(ToolEvent(
        tool_id=shop["disc"].id, worker_id=shop["worker"].id, recorded_by_id=shop["office"].id,
        type=ToolEventType.ISSUE, quantity=Decimal(qty), stock_delta=-Decimal(qty),
        created_at=shop_local_to_utc(day, time(10, 0)),
    ))


def test_forecast_uses_stock_on_hand_and_the_rate_of_hand_outs(app, shop):
    today = shop_now().date()
    # Two a day on four days, five days apart: about 0.36 a day (SBA).
    for back in (20, 15, 10, 5):
        _hand_out_on(shop, today - timedelta(days=back), 2)
    shop["disc"].quantity_on_hand = Decimal("3")
    db.session.commit()

    item = next(i for i in replenishment_service.replenishment(today)["items"] if i["toolId"] == shop["disc"].id)
    assert item["basis"] == "USAGE" and item["handOuts"] == 4
    assert item["onHand"] == 3
    assert item["dailyUse"] == pytest.approx(0.36, abs=0.01)
    # No supplier order history: 7 days to deliver, plus the 3 safety days.
    assert item["leadDays"] == 7 and item["safetyDays"] == 3
    assert item["reorderPoint"] == pytest.approx(3.6, abs=0.1)
    # 3 on hand is under the reorder point, so it is due now.
    assert item["status"] == "OVERDUE"
    assert item["runOutOn"] == (today + timedelta(days=8)).isoformat()
    assert item["usualQuantity"] >= 1

    # Plenty on the shelf: OK, and the run-out date moves out.
    shop["disc"].quantity_on_hand = Decimal("40")
    db.session.commit()
    item = next(i for i in replenishment_service.replenishment(today)["items"] if i["toolId"] == shop["disc"].id)
    assert item["status"] == "OK"
    assert date.fromisoformat(item["nextPurchaseOn"]) > today + timedelta(days=30)


def test_lead_time_comes_from_received_supplier_orders_and_undone_hand_outs_do_not_count(app, shop):
    today = shop_now().date()
    sup = Supplier(name="CI Supplier", code="CIS")
    db.session.add(sup)
    db.session.flush()
    db.session.add(MaterialPurchase(
        tool_id=shop["disc"].id, supplier_id=sup.id, material_name="Cutting disc", quantity=Decimal("20"),
        unit="pcs", date_ordered=today - timedelta(days=30), date_received=today - timedelta(days=26),
    ))
    for back in (20, 15, 10, 5):
        _hand_out_on(shop, today - timedelta(days=back), 2)
    db.session.commit()
    assert replenishment_service.lead_days([shop["disc"].id]) == {shop["disc"].id: 4}

    for e in ToolEvent.query.filter_by(type=ToolEventType.ISSUE).all()[:2]:
        e.undone_at = datetime.now(timezone.utc)
    db.session.commit()
    # Two days of hand-outs left: not enough, so it falls back to purchase history.
    item = next(i for i in replenishment_service.replenishment(today)["items"] if i["toolId"] == shop["disc"].id)
    assert item["handOuts"] == 2 and item["basis"] == "PURCHASES"


def _job_with_material(worker, status=OperationStatus.SCHEDULED, received=False):
    from app.models.client import Client

    from app.models.supplier import Supplier

    client = Client.query.filter_by(name="CI Client2").first()
    supplier = Supplier.query.filter_by(code="CIM").first()
    if client is None:
        client = Client(name="CI Client2")
        supplier = Supplier(name="CI Metals", code="CIM")
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
