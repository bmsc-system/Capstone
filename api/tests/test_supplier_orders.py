"""Consolidated supplier purchase orders: lines from several jobs on one PO.

Uses the bmsc_test database from conftest (schema built from the models).
"""

from datetime import date
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from tests.catalog_helpers import ensure_material

from app.extensions import bcrypt, db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.material_purchase import MaterialPurchase
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder, SupplierOrderStatus
from app.models.user import User, UserRole, UserStatus


def _user(email, role):
    user = User(
        email=email,
        password_hash=bcrypt.generate_password_hash("Passw0rd!").decode("utf-8"),
        full_name=email.split("@")[0],
        role=role,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _headers(user):
    token = create_access_token(identity=user.id, additional_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


def _job(shop, title):
    job = JobOrder(
        client_id=shop.get("client_id") or shop["client"].id,
        title=title,
        due_date=date(2031, 6, 1),
        status=JobOrderStatus.DRAFT,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        material_status=MaterialStatus.TO_ORDER,
        created_by_id=shop.get("office_id") or shop["office"].id,
    )
    db.session.add(job)
    db.session.commit()
    return job


@pytest.fixture
def shop(app):
    admin = _user("so_admin@test.local", UserRole.ADMIN)
    office = _user("so_office@test.local", UserRole.OFFICE_STAFF)
    client_row = Client(name="PO Client")
    steel = Supplier(name="PO Steel", code="PSL", typical_lead_time_days=5, address="1 Mill Rd")
    other = Supplier(name="Other Metals", code="OTM", typical_lead_time_days=2)
    db.session.add_all([client_row, steel, other])
    db.session.flush()
    # Capture PKs before commit expires the instances (expire_on_commit).
    s = {
        "admin": admin,
        "office": office,
        "client": client_row,
        "client_id": client_row.id,
        "steel": steel,
        "other": other,
        "admin_id": admin.id,
        "office_id": office.id,
    }
    db.session.commit()
    s["job_a"] = _job(s, "Shaft A")
    s["job_b"] = _job(s, "Shaft B")
    return s


def _line(job, name="Round bar 50mm", quantity=4, unit_cost=250, **extra):
    return {
        "jobOrderId": job.id,
        "materialCatalogId": ensure_material(name),
        "materialName": name,
        "quantity": quantity,
        "unit": "pcs",
        "unitCost": unit_cost,
        **extra,
    }


def _add_lines(client, shop, lines, supplier=None, user=None):
    return client.post(
        "/api/v1/supplier-orders/draft-lines",
        json={"supplierId": (supplier or shop["steel"]).id, "lines": lines},
        headers=_headers(user or shop["office"]),
    )


def _both_jobs_draft(client, shop):
    res = _add_lines(
        client,
        shop,
        [
            _line(shop["job_a"], quantity=4, gradeOrSpec="AISI 1045"),
            _line(shop["job_b"], quantity=6, gradeOrSpec="aisi 1045"),
        ],
    )
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _issue(client, shop, order_id, user=None, when="2031-03-02"):
    return client.post(
        f"/api/v1/supplier-orders/{order_id}/issue",
        json={"dateIssued": when},
        headers=_headers(user or shop["office"]),
    )


def _print(client, shop, order_id, user=None):
    return client.post(
        f"/api/v1/supplier-orders/{order_id}/print",
        headers=_headers(user or shop["office"]),
    )


def _receive(client, shop, order_id, when, user=None, print_first=True):
    if print_first:
        _print(client, shop, order_id, user)
    return client.post(
        f"/api/v1/supplier-orders/{order_id}/receive",
        json={"receivedDate": when},
        headers=_headers(user or shop["office"]),
    )


def _job_body(client, shop, job):
    return client.get(f"/api/v1/job-orders/{job.id}", headers=_headers(shop["admin"])).get_json()


def test_lines_from_two_jobs_on_one_order(client, shop):
    order = _both_jobs_draft(client, shop)
    assert order["status"] == "DRAFT"
    assert order["poNumber"] is None
    assert order["lineCount"] == 2 and order["jobCount"] == 2
    assert {ln["jobOrderId"] for ln in order["lines"]} == {shop["job_a"].id, shop["job_b"].id}
    assert all(ln["plannedMaterialId"] is None for ln in order["lines"])

    # "Order materials" from a job's page lands on the same open draft.
    res = client.post(
        f"/api/v1/job-orders/{shop['job_a'].id}/material-purchases",
        json={"supplierId": shop["steel"].id, **_line(shop["job_a"], "Cutting disc", 2, 40)},
        headers=_headers(shop["office"]),
    )
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["supplierOrderId"] == order["id"]

    # A different supplier gets its own draft.
    other = _add_lines(
        client, shop, [_line(shop["job_a"], "Bolts", 1, 5)], supplier=shop["other"]
    ).get_json()
    assert other["id"] != order["id"]

    for ln in MaterialPurchase.query.filter(MaterialPurchase.supplier_order_id.isnot(None)):
        assert ln.supplier_id == ln.supplier_order.supplier_id

    # Draft lines do not count as ordered.
    assert _job_body(client, shop, shop["job_a"])["materialStatus"] == "TO_ORDER"


def test_issuing_assigns_number_and_locks_lines(client, shop):
    order = _both_jobs_draft(client, shop)
    line_id = order["lines"][0]["id"]

    res = _issue(client, shop, order["id"], user=shop["admin"])
    assert res.status_code == 403

    res = _issue(client, shop, order["id"])
    assert res.status_code == 200, res.get_json()
    issued = res.get_json()
    assert issued["status"] == "ISSUED"
    assert issued["poNumber"] == "PSL31000001"
    assert issued["dateIssued"] == "2031-03-02"
    assert issued["expectedDeliveryDate"] == "2031-03-07"
    assert issued["issuedById"] == shop["office"].id
    assert all(ln["dateOrdered"] == "2031-03-02" for ln in issued["lines"])

    headers = _headers(shop["office"])
    res = client.patch(
        f"/api/v1/supplier-orders/{order['id']}/lines/{line_id}", json={"quantity": 1}, headers=headers
    )
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "ORDER_LOCKED"
    assert "cancel the order" in res.get_json()["error"]["message"]
    res = client.delete(f"/api/v1/supplier-orders/{order['id']}/lines/{line_id}", headers=headers)
    assert res.status_code == 409
    res = client.patch(
        f"/api/v1/job-orders/{shop['job_a'].id}/material-purchases/{line_id}",
        json={"quantity": 1},
        headers=headers,
    )
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "ON_SUPPLIER_ORDER"
    # Lines can no longer be cancelled or split one by one.
    for path in ("cancel", "split"):
        res = client.post(
            f"/api/v1/supplier-orders/{order['id']}/lines/{line_id}/{path}",
            json={"quantity": 1},
            headers=headers,
        )
        assert res.status_code in (404, 405)

    body = _job_body(client, shop, shop["job_a"])
    assert body["materialStatus"] == "ORDERED"
    assert body["materialReadiness"]["expectedDate"] == "2031-03-07"


def test_po_numbers_run_per_supplier_per_year(client, shop):
    def issue_one(supplier, when):
        order = _add_lines(client, shop, [_line(shop["job_a"], "Bolts", 1, 5)], supplier=supplier)
        res = _issue(client, shop, order.get_json()["id"], when=when)
        assert res.status_code == 200, res.get_json()
        return res.get_json()["poNumber"]

    assert issue_one(shop["steel"], "2031-03-02") == "PSL31000001"
    assert issue_one(shop["steel"], "2031-03-03") == "PSL31000002"
    assert issue_one(shop["other"], "2031-03-03") == "OTM31000001"
    assert issue_one(shop["steel"], "2032-01-05") == "PSL32000001"
    order = SupplierOrder.query.filter_by(po_number="PSL32000001").one()
    assert (order.po_year, order.po_seq) == (2032, 1)


def test_existing_po_numbers_are_kept(client, shop):
    old = SupplierOrder(
        supplier_id=shop["steel"].id,
        status=SupplierOrderStatus.RECEIVED,
        po_seq=88,
        po_number="BMSC-PO-00088",
        date_issued=date(2031, 1, 5),
        prepared_by_id=shop["office"].id,
    )
    db.session.add(old)
    db.session.commit()
    order = _both_jobs_draft(client, shop)
    assert _issue(client, shop, order["id"]).get_json()["poNumber"] == "PSL31000001"
    assert db.session.get(SupplierOrder, old.id).po_number == "BMSC-PO-00088"


def test_supplier_without_a_code_cannot_issue(client, shop):
    nocode = Supplier(name="No Code Steel", typical_lead_time_days=3)
    db.session.add(nocode)
    db.session.commit()
    order = _add_lines(client, shop, [_line(shop["job_a"])], supplier=nocode).get_json()
    res = _issue(client, shop, order["id"])
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == "SUPPLIER_CODE_MISSING"


def test_cancelling_the_order_returns_material_to_order(client, shop):
    order = _both_jobs_draft(client, shop)
    _issue(client, shop, order["id"])
    assert _job_body(client, shop, shop["job_a"])["materialStatus"] == "ORDERED"

    res = client.post(
        f"/api/v1/supplier-orders/{order['id']}/cancel", headers=_headers(shop["office"])
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "CANCELLED"
    for job in (shop["job_a"], shop["job_b"]):
        body = _job_body(client, shop, job)
        assert body["materialStatus"] == "TO_ORDER"
        assert body["materialReadiness"]["notOrderedYet"] is True


def test_receive_order_receives_every_line_on_one_date(client, shop):
    order = _both_jobs_draft(client, shop)
    _issue(client, shop, order["id"])

    res = _receive(client, shop, order["id"], "2031-03-09")
    assert res.status_code == 200, res.get_json()
    closed = res.get_json()
    assert closed["status"] == "RECEIVED"
    assert closed["receivedDate"] == "2031-03-09"
    assert {ln["dateReceived"] for ln in closed["lines"]} == {"2031-03-09"}
    for job in (shop["job_a"], shop["job_b"]):
        body = _job_body(client, shop, job)
        assert body["materialStatus"] == "RECEIVED"
        assert body["materialReceivedDate"] == "2031-03-09"

    res = _receive(client, shop, order["id"], "2031-03-10")
    assert res.status_code == 409

    # Lead time is measured per order: issued 03-02, received 03-09 = 7 days.
    res = client.get(
        "/api/v1/analytics/purchasing?from=2031-03-01&to=2031-03-31",
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    lead = next(
        r for r in res.get_json()["supplierLeadTime"] if r["supplierId"] == shop["steel"].id
    )
    assert lead["sampleCount"] == 1
    assert lead["averageActualDays"] == 7


def test_mixed_line_receipts_stay_issued_not_partially_received(client, shop):
    """Some lines received, some not → ISSUED (PARTIALLY_RECEIVED is unreachable)."""
    from app.services.supplier_order_service import recompute_order_status

    order = _both_jobs_draft(client, shop)
    _issue(client, shop, order["id"])
    first = db.session.get(MaterialPurchase, order["lines"][0]["id"])
    first.date_received = date(2031, 3, 5)
    so = db.session.get(SupplierOrder, order["id"])
    recompute_order_status(so)
    db.session.flush()
    assert so.status == SupplierOrderStatus.ISSUED


def test_partly_received_order_is_completed_by_receive_order(client, shop):
    order = _both_jobs_draft(client, shop)
    _issue(client, shop, order["id"])
    # Legacy mixed-receipt row (status may still be PARTIALLY_RECEIVED in old data).
    first = db.session.get(MaterialPurchase, order["lines"][0]["id"])
    first.date_received = date(2031, 3, 5)
    so = db.session.get(SupplierOrder, order["id"])
    so.status = SupplierOrderStatus.PARTIALLY_RECEIVED
    db.session.commit()

    res = client.post(
        f"/api/v1/supplier-orders/{order['id']}/cancel", headers=_headers(shop["office"])
    )
    assert res.status_code == 409
    assert "Receive order" in res.get_json()["error"]["message"]

    res = _receive(client, shop, order["id"], "2031-03-08")
    assert res.status_code == 200, res.get_json()
    done = res.get_json()
    assert done["status"] == "RECEIVED"
    received = {ln["id"]: ln["dateReceived"] for ln in done["lines"]}
    assert received[order["lines"][0]["id"]] == "2031-03-05"
    assert received[order["lines"][1]["id"]] == "2031-03-08"


def test_printout_combines_same_material_lines(client, shop):
    order = _both_jobs_draft(client, shop)
    _add_lines(client, shop, [_line(shop["job_b"], "Plate 10mm", 1, 900)])
    client.patch(
        f"/api/v1/supplier-orders/{order['id']}",
        json={"vatRate": 12},
        headers=_headers(shop["office"]),
    )
    _issue(client, shop, order["id"])

    res = client.get(
        f"/api/v1/supplier-orders/{order['id']}/print", headers=_headers(shop["office"])
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert data["order"]["poNumber"] == "PSL31000001"
    assert data["supplier"]["address"] == "1 Mill Rd"
    assert len(data["rows"]) == 2
    bar = next(r for r in data["rows"] if r["materialName"] == "Round bar 50mm")
    assert bar["quantity"] == 10
    assert bar["lineCount"] == 2
    assert "jobNumbers" not in bar
    assert bar["amount"] == 2500
    assert data["subtotal"] == 3400
    assert data["vatAmount"] == 408
    assert data["total"] == 3808

    # The system keeps one line per job.
    detail = client.get(
        f"/api/v1/supplier-orders/{order['id']}", headers=_headers(shop["office"])
    ).get_json()
    assert len(detail["lines"]) == 3


def test_draft_and_cancelled_lines_do_not_count_as_ordered(client, shop):
    from app.models.operation import JobOperation, OperationStatus
    from app.models.user import UserRole as Role
    from app.services import operation_service
    from app.utils.errors import AppError

    job = shop["job_a"]
    job.status = JobOrderStatus.SCHEDULED
    op = JobOperation(
        job_order_id=job.id,
        sequence_no=1,
        operation_name="Turning",
        estimated_hours=Decimal("1"),
        status=OperationStatus.SCHEDULED,
        assigned_worker_id=shop["admin"].id,
    )
    db.session.add(op)
    db.session.commit()
    order = _both_jobs_draft(client, shop)
    headers = _headers(shop["admin"])

    # Draft: no date ordered, status DRAFT, still to order, no expected date.
    line_a = next(ln for ln in order["lines"] if ln["jobOrderId"] == job.id)
    assert line_a["dateOrdered"] is None and line_a["status"] == "DRAFT"
    body = _job_body(client, shop, job)
    assert body["materialStatus"] == "TO_ORDER"
    assert body["materialReadiness"]["source"] != "PURCHASE_LINES"
    assert body["materialReadiness"]["expectedDate"] is None
    assert not body["materialReadiness"].get("lines")
    db.session.expire_all()
    with pytest.raises(AppError) as exc:
        operation_service.start_operation(
            JobOperation.query.get(op.id), shop["admin"].id, Role.ADMIN.value, None
        )
    assert exc.value.code == "MATERIALS_NOT_ORDERED"

    # Inventory and analytics leave draft lines out without erroring on the empty date.
    inv = client.get(
        "/api/v1/inventory/material-purchases?from=2031-01-01&to=2031-12-31", headers=headers
    )
    assert inv.status_code == 200, inv.get_json()
    assert inv.get_json()["summary"]["purchaseCount"] == 0
    ana = client.get("/api/v1/analytics/purchasing?from=2031-01-01&to=2031-12-31", headers=headers)
    assert ana.status_code == 200 and ana.get_json()["purchaseCount"] == 0

    # Issue, then cancel the order: its lines leave spend, analytics and the gate.
    _issue(client, shop, order["id"])
    res = client.post(
        f"/api/v1/supplier-orders/{order['id']}/cancel", headers=_headers(shop["office"])
    )
    assert res.status_code == 200, res.get_json()
    inv = client.get(
        "/api/v1/inventory/material-purchases?from=2031-01-01&to=2031-12-31", headers=headers
    ).get_json()
    assert inv["summary"]["purchaseCount"] == 0
    ana = client.get(
        "/api/v1/analytics/purchasing?from=2031-01-01&to=2031-12-31", headers=headers
    ).get_json()
    assert ana["purchaseCount"] == 0
    db.session.expire_all()
    with pytest.raises(AppError) as exc:
        operation_service.start_operation(
            JobOperation.query.get(op.id), shop["admin"].id, Role.ADMIN.value, None
        )
    assert exc.value.code == "MATERIALS_NOT_ORDERED"


def test_existing_lines_stay_without_a_po(client, shop):
    legacy = MaterialPurchase(
        job_order_id=shop["job_a"].id,
        material_name="Old stock",
        quantity=Decimal("1"),
        unit="pcs",
        unit_cost=Decimal("10"),
        supplier_id=shop["steel"].id,
        date_ordered=date(2031, 1, 5),
    )
    db.session.add(legacy)
    db.session.commit()
    body = client.get(
        f"/api/v1/job-orders/{shop['job_a'].id}/material-purchases",
        headers=_headers(shop["office"]),
    ).get_json()
    assert body[0]["supplierOrderId"] is None and body[0]["poNumber"] is None
    assert body[0]["status"] == "ORDERED"
    # Still editable, since it is not on a PO.
    res = client.patch(
        f"/api/v1/job-orders/{shop['job_a'].id}/material-purchases/{legacy.id}",
        json={"quantity": 2},
        headers=_headers(shop["office"]),
    )
    assert res.status_code == 200, res.get_json()


@pytest.fixture
def one_day(shop):
    supplier = Supplier(name="Next Day Steel", code="NDS", typical_lead_time_days=1)
    db.session.add(supplier)
    db.session.commit()
    return supplier


def _one_line_order(client, shop, supplier, issued):
    order = _add_lines(client, shop, [_line(shop["job_a"])], supplier=supplier).get_json()
    res = _issue(client, shop, order["id"], when=issued)
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def test_saturday_order_from_one_day_supplier_is_promised_for_monday(client, shop, one_day):
    from app.services.supplier_reliability_service import supplier_reliability

    order = _one_line_order(client, shop, one_day, "2031-03-01")  # Saturday
    assert order["expectedDeliveryDate"] == "2031-03-03"  # Monday, not Sunday

    res = _receive(client, shop, order["id"], "2031-03-03")
    assert res.status_code == 200, res.get_json()
    row = next(r for r in supplier_reliability(today=date(2031, 3, 10)) if r["supplierId"] == one_day.id)
    assert row["dueDeliveries"] == 1 and row["onTimeDeliveries"] == 1 and row["lateDeliveries"] == 0


def test_promised_date_skips_shop_holidays(client, shop, one_day):
    from app.models.worker_skill import CalendarExceptionType, WorkCalendarException

    db.session.add(
        WorkCalendarException(date=date(2031, 3, 3), type=CalendarExceptionType.HOLIDAY_NO_WORK)
    )
    db.session.commit()
    order = _one_line_order(client, shop, one_day, "2031-03-01")
    assert order["expectedDeliveryDate"] == "2031-03-04"  # past Sunday and the Monday holiday


def test_edited_expected_date_moves_off_sunday(client, shop, one_day):
    order = _one_line_order(client, shop, one_day, "2031-03-04")
    res = client.patch(
        f"/api/v1/supplier-orders/{order['id']}/expected-delivery",
        json={"expectedDeliveryDate": "2031-03-09", "note": "Supplier said Sunday"},  # Sunday
        headers=_headers(shop["office"]),
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["expectedDeliveryDate"] == "2031-03-10"


def test_stored_sunday_promise_counts_from_next_working_day(client, shop, one_day):
    order = _one_line_order(client, shop, one_day, "2031-03-04")
    row = db.session.get(MaterialPurchase, order["lines"][0]["id"])
    row.supplier_order.expected_delivery_date = date(2031, 3, 9)  # Sunday, saved before this rule
    db.session.commit()
    assert row.promised_date == date(2031, 3, 10)


def test_same_material_for_two_jobs_on_one_po_is_stocked_per_job(client, shop):
    """One PO carries the same material for two jobs: it prints as one row, both
    lines sit in stock after receipt, and starting job A uses only A's line
    while B's stays on hand, reserved for B."""
    from datetime import datetime, timezone

    from app.services.material_purchase_service import consume_received_lines

    order = _both_jobs_draft(client, shop)
    _issue(client, shop, order["id"])
    printed = client.get(
        f"/api/v1/supplier-orders/{order['id']}/print", headers=_headers(shop["office"])
    ).get_json()
    assert len(printed["rows"]) == 1 and printed["rows"][0]["quantity"] == 10

    assert _receive(client, shop, order["id"], "2031-03-09").status_code == 200

    def stock(status):
        res = client.get(
            f"/api/v1/inventory/material-purchases?status={status}",
            headers=_headers(shop["office"]),
        )
        assert res.status_code == 200, res.get_json()
        body = res.get_json()
        rows = body["items"] if isinstance(body, dict) else body
        return {r["jobOrderId"]: r["quantity"] for r in rows}

    assert stock("RECEIVED") == {shop["job_a"].id: 4, shop["job_b"].id: 6}

    consume_received_lines(db.session.get(JobOrder, shop["job_a"].id), datetime.now(timezone.utc))
    db.session.flush()
    assert stock("RECEIVED") == {shop["job_b"].id: 6}
    assert stock("CONSUMED") == {shop["job_a"].id: 4}


def test_inventory_analytics_raw_materials(client, shop):
    """Analytics → Inventory → Raw materials: what the jobs bought, how long it
    took to arrive, and which jobs are still waiting."""
    order = _both_jobs_draft(client, shop)

    def raw():
        res = client.get(
            "/api/v1/analytics/inventory?from=2031-03-01&to=2031-03-31",
            headers=_headers(shop["office"]),
        )
        assert res.status_code == 200, res.get_json()
        return res.get_json()

    # A draft PO is not bought yet.
    assert raw()["rawMaterials"]["boughtLines"] == 0

    _issue(client, shop, order["id"], when="2031-03-02")
    waiting = raw()["rawMaterials"]
    assert waiting["boughtLines"] == 2 and waiting["boughtJobs"] == 2
    assert waiting["waitingJobs"] == 2 and len(waiting["waiting"]) == 2

    assert _receive(client, shop, order["id"], "2031-03-09").status_code == 200
    body = raw()
    mats = body["rawMaterials"]
    assert mats["waitingJobs"] == 0
    assert mats["mostUsed"][0]["jobs"] == 2 and mats["mostUsed"][0]["lines"] == 2
    assert mats["deliveries"] == 2 and mats["avgDeliveryDays"] == 7.0
    assert len(mats["deliveryByMaterial"]) == 1  # "AISI 1045" and "aisi 1045" are one material
    assert mats["deliveryByMaterial"][0]["avgDays"] == 7.0
    assert body["consumables"]["useQty"] == 0
