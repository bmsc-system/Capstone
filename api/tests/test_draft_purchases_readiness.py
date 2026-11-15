"""Office records purchases on drafts; scheduling waits for the purchase lines.

Uses the bmsc_test database from conftest (schema built from the models).
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from tests.catalog_helpers import approve_issue, ensure_material
from app.extensions import bcrypt, db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.material_purchase import MaterialPurchase
from app.models.operation import JobOperation, OperationStatus
from app.models.supplier import Supplier
from app.models.user import User, UserRole, UserStatus
from app.models.worker_skill import WorkerSchedule
from app.services.schedule_calendar import shop_now, utc_to_shop


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


@pytest.fixture
def shop(app):
    admin = _user("rd_admin@test.local", UserRole.ADMIN)
    office = _user("rd_office@test.local", UserRole.OFFICE_STAFF)
    client_row = Client(name="Readiness Client")
    fast = Supplier(name="Fast Steel", code="FST", typical_lead_time_days=3)
    slow = Supplier(name="Slow Alloys", code="SLA", typical_lead_time_days=7)
    unknown = Supplier(name="No Lead Co")
    db.session.add_all([client_row, fast, slow, unknown])
    db.session.commit()
    return {
        "admin": admin,
        "office": office,
        "client": client_row,
        "fast": fast,
        "slow": slow,
        "unknown": unknown,
    }


def _draft(shop, *, expected=None):
    job = JobOrder(
        client_id=shop["client"].id,
        title="Readiness Job",
        due_date=date(2031, 6, 1),
        status=JobOrderStatus.DRAFT,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        material_status=MaterialStatus.TO_ORDER,
        material_expected_date=expected,
        created_by_id=shop["office"].id,
    )
    db.session.add(job)
    db.session.flush()
    db.session.add(
        JobOperation(
            job_order_id=job.id,
            sequence_no=1,
            operation_name="Cutting",
            estimated_hours=Decimal("2"),
            status=OperationStatus.PENDING,
        )
    )
    db.session.commit()
    return job


def _line(job, supplier, ordered, received=None, name="Plate"):
    p = MaterialPurchase(
        job_order_id=job.id,
        material_name=name,
        quantity=Decimal("1"),
        unit="pcs",
        unit_cost=Decimal("100"),
        supplier_id=supplier.id,
        date_ordered=ordered,
        date_received=received,
    )
    db.session.add(p)
    db.session.commit()
    return p


def test_office_opens_draft_detail_and_records_purchase(client, shop):
    job = _draft(shop)
    headers = _headers(shop["office"])

    res = client.get(f"/api/v1/job-orders/{job.id}", headers=headers)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "DRAFT"

    res = client.post(
        f"/api/v1/job-orders/{job.id}/material-purchases",
        json={
            "materialCatalogId": ensure_material("A36 plate 10mm"), "materialName": "A36 plate 10mm",
            "supplierId": shop["fast"].id,
            "quantity": 2,
            "unit": "pcs",
            "unitCost": 850,
            "dateOrdered": "2031-03-02",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.get_json()
    order_id = res.get_json()["supplierOrderId"]
    assert res.get_json()["status"] == "DRAFT"

    # On a draft PO the material is not ordered yet.
    body = client.get(f"/api/v1/job-orders/{job.id}", headers=headers).get_json()
    assert body["materialStatus"] == "TO_ORDER"

    res = approve_issue(client, f"/api/v1/supplier-orders/{order_id}", {"dateIssued": "2031-03-02"}, _headers(shop["office"]))
    assert res.status_code == 200, res.get_json()

    body = client.get(f"/api/v1/job-orders/{job.id}", headers=headers).get_json()
    assert body["status"] == "DRAFT"
    assert body["materialStatus"] == "ORDERED"
    assert body["materialReadiness"]["source"] == "PURCHASE_LINES"
    assert body["materialReadiness"]["expectedDate"] == "2031-03-05"


def test_derived_expected_date_is_latest_outstanding_line(client, shop):
    job = _draft(shop, expected=date(2031, 3, 1))
    _line(job, shop["fast"], date(2031, 3, 1), name="Flat bar")  # 03-04
    slow = _line(job, shop["slow"], date(2031, 3, 2), name="Alloy rod")  # 03-09
    _line(job, shop["fast"], date(2031, 2, 20), received=date(2031, 3, 5), name="Bolts")

    body = client.get(
        f"/api/v1/job-orders/{job.id}", headers=_headers(shop["admin"])
    ).get_json()
    readiness = body["materialReadiness"]
    assert readiness["expectedDate"] == "2031-03-09"
    assert readiness["limitingLine"]["purchaseId"] == slow.id
    assert readiness["limitingLine"]["basis"] == "LEAD_TIME"
    assert {l["basis"] for l in readiness["lines"]} == {"LEAD_TIME", "RECEIVED"}

    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/propose",
        json={},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    not_before = utc_to_shop(datetime.fromisoformat(data["materialNotBefore"]))
    assert not_before.date() == date(2031, 3, 9)
    assert "Alloy rod from Slow Alloys" in data["materialConstraintReason"]


def test_received_line_uses_actual_received_date(client, shop):
    job = _draft(shop)
    _line(job, shop["fast"], date(2031, 3, 1))  # expected 03-04
    _line(job, shop["fast"], date(2031, 3, 1), received=date(2031, 3, 12), name="Late")

    body = client.get(
        f"/api/v1/job-orders/{job.id}", headers=_headers(shop["admin"])
    ).get_json()
    assert body["materialReadiness"]["expectedDate"] == "2031-03-12"
    assert body["materialReadiness"]["limitingLine"]["basis"] == "RECEIVED"


def test_supplier_without_lead_time_leaves_date_unknown_and_blocks_proposal(client, shop):
    job = _draft(shop)
    _line(job, shop["fast"], date(2031, 3, 1))
    _line(job, shop["unknown"], date(2031, 3, 6), name="Brass")
    headers = _headers(shop["admin"])

    readiness = client.get(f"/api/v1/job-orders/{job.id}", headers=headers).get_json()[
        "materialReadiness"
    ]
    assert readiness["expectedDate"] is None
    assert readiness["missingLeadTimeSuppliers"] == ["No Lead Co"]
    brass = next(l for l in readiness["lines"] if l["materialName"] == "Brass")
    assert brass["basis"] == "UNKNOWN" and brass["expectedArrival"] is None

    res = client.post(f"/api/v1/job-orders/{job.id}/schedule/propose", json={}, headers=headers)
    assert res.status_code == 400
    err = res.get_json()["error"]
    assert err["code"] == "MATERIAL_DATE_UNKNOWN"
    assert "No Lead Co" in err["message"]


def test_old_typed_date_does_not_cover_supplier_without_lead_time(client, shop):
    job = _draft(shop, expected=date(2031, 3, 11))
    _line(job, shop["fast"], date(2031, 3, 1))  # 03-04
    _line(job, shop["unknown"], date(2031, 3, 6), name="Brass")
    headers = _headers(shop["admin"])

    readiness = client.get(f"/api/v1/job-orders/{job.id}", headers=headers).get_json()[
        "materialReadiness"
    ]
    assert readiness["expectedDate"] is None
    assert readiness["missingLeadTimeSuppliers"] == ["No Lead Co"]

    res = client.post(f"/api/v1/job-orders/{job.id}/schedule/propose", json={}, headers=headers)
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == "MATERIAL_DATE_UNKNOWN"


def test_supplier_lead_time_is_required(client, shop):
    headers = _headers(shop["office"])
    res = client.post("/api/v1/suppliers", json={"name": "Nameless Lead"}, headers=headers)
    assert res.status_code == 400
    res = client.post(
        "/api/v1/suppliers", json={"name": "Blank Lead", "typicalLeadTimeDays": ""}, headers=headers
    )
    assert res.status_code == 400

    res = client.patch(
        f"/api/v1/suppliers/{shop['fast'].id}",
        json={"typicalLeadTimeDays": None},
        headers=headers,
    )
    assert res.status_code == 400
    # A legacy supplier without one must get one before any edit saves.
    res = client.patch(
        f"/api/v1/suppliers/{shop['unknown'].id}", json={"phone": "0917"}, headers=headers
    )
    assert res.status_code == 400
    res = client.patch(
        f"/api/v1/suppliers/{shop['unknown'].id}",
        json={"typicalLeadTimeDays": 4},
        headers=headers,
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["typicalLeadTimeDays"] == 4
    db.session.expire_all()
    assert Supplier.query.get(shop["fast"].id).typical_lead_time_days == 3


def test_old_typed_expected_date_ignored_when_no_lines(client, shop):
    job = _draft(shop, expected=date(2031, 3, 20))
    readiness = client.get(
        f"/api/v1/job-orders/{job.id}", headers=_headers(shop["admin"])
    ).get_json()["materialReadiness"]
    assert readiness["source"] is None
    assert readiness["expectedDate"] is None
    assert readiness["supplierOrders"] == []


def _not_before_date(data):
    return utc_to_shop(datetime.fromisoformat(data["materialNotBefore"])).date()


def test_unordered_job_schedules_after_longest_active_lead_time(client, shop):
    job = _draft(shop)
    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/propose",
        json={},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert _not_before_date(data) == shop_now().date() + timedelta(days=7)
    assert "longest supplier lead time" in data["materialConstraintReason"]


def test_inactive_supplier_lead_time_is_ignored(client, shop):
    shop["slow"].active = False
    db.session.commit()
    job = _draft(shop)
    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/propose", json={}, headers=_headers(shop["admin"])
    )
    assert res.status_code == 200, res.get_json()
    assert _not_before_date(res.get_json()) == shop_now().date() + timedelta(days=3)


def test_ordered_job_waits_for_its_lines_not_the_lead_time_floor(client, shop):
    job = _draft(shop)
    _line(job, shop["fast"], shop_now().date(), name="Plate")
    headers = _headers(shop["admin"])

    res = client.post(f"/api/v1/job-orders/{job.id}/schedule/propose", json={}, headers=headers)
    assert res.status_code == 200, res.get_json()
    # Ordered: the Plate's 3-day arrival applies, not today + 7.
    assert _not_before_date(res.get_json()) < shop_now().date() + timedelta(days=7)

    _line(job, shop["slow"], date(2031, 3, 2), name="Rod")
    res = client.post(f"/api/v1/job-orders/{job.id}/schedule/propose", json={}, headers=headers)
    assert res.status_code == 200, res.get_json()
    assert _not_before_date(res.get_json()) == date(2031, 3, 9)


def test_draft_supplier_order_does_not_count_as_ordered(client, shop):
    job = _draft(shop)
    headers = _headers(shop["office"])
    res = client.post(
        f"/api/v1/job-orders/{job.id}/material-purchases",
        json={
            "materialCatalogId": ensure_material("A36 plate"), "materialName": "A36 plate",
            "supplierId": shop["fast"].id,
            "quantity": 1,
            "unitCost": 100,
            "dateOrdered": "2031-03-02",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.get_json()

    body = client.get(f"/api/v1/job-orders/{job.id}", headers=headers).get_json()
    orders = body["materialReadiness"]["supplierOrders"]
    assert [(o["supplierName"], o["status"]) for o in orders] == [("Fast Steel", "DRAFT")]

    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/propose", json={}, headers=_headers(shop["admin"])
    )
    assert res.status_code == 200, res.get_json()
    assert "longest supplier lead time" in res.get_json()["materialConstraintReason"]


def test_not_required_job_schedules_normally(client, shop):
    job = _draft(shop)
    job.material_status = MaterialStatus.NOT_REQUIRED
    worker = _user("rd_worker@test.local", UserRole.PRODUCTION_WORKER)
    for dow in range(7):
        db.session.add(
            WorkerSchedule(
                worker_id=worker.id,
                day_of_week=dow,
                is_working=dow < 6,
                start_time=time(8, 0) if dow < 6 else None,
                end_time=time(17, 0) if dow < 6 else None,
            )
        )
    job.operations[0].assigned_worker_id = worker.id
    db.session.commit()

    res = client.post(
        f"/api/v1/job-orders/{job.id}/schedule/propose",
        json={},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert data["materialNotBefore"] is None
    assert data["operations"][0]["scheduledStart"]


def test_draft_proposal_for_to_order_uses_lead_time_floor(client, shop):
    res = client.post(
        "/api/v1/job-orders/schedule/propose",
        json={
            "dueDate": "2031-06-01",
            "materialStatus": "TO_ORDER",
            "operations": [{"operationName": "Cutting", "estimatedHours": 2}],
        },
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 200, res.get_json()
    assert _not_before_date(res.get_json()) == shop_now().date() + timedelta(days=7)


def test_expected_arrival_matches_latest_outstanding_po_line(client, shop):
    job = _draft(shop)
    headers = _headers(shop["office"])
    for supplier, name in ((shop["fast"], "Flat bar"), (shop["slow"], "Alloy rod")):
        res = client.post(
            f"/api/v1/job-orders/{job.id}/material-purchases",
            json={
                "materialCatalogId": ensure_material(name),
                "materialName": name,
                "supplierId": supplier.id,
                "quantity": 1,
                "unitCost": 100,
                "dateOrdered": "2031-03-02",
            },
            headers=headers,
        )
        assert res.status_code == 201, res.get_json()
        res = approve_issue(client, f"/api/v1/supplier-orders/{res.get_json()['supplierOrderId']}", {"dateIssued": "2031-03-02"}, _headers(shop["office"]))
        assert res.status_code == 200, res.get_json()
    _line(job, shop["fast"], date(2031, 2, 20), received=date(2031, 3, 5), name="Bolts")

    readiness = client.get(f"/api/v1/job-orders/{job.id}", headers=headers).get_json()[
        "materialReadiness"
    ]
    outstanding = [l for l in readiness["lines"] if l["basis"] != "RECEIVED"]
    latest = max(outstanding, key=lambda l: l["expectedArrival"])
    assert latest["materialName"] == "Alloy rod"
    # Issued + 7 days lands on Sunday 9 Mar; expected moves to Monday.
    assert readiness["expectedDate"] == latest["expectedArrival"] == "2031-03-10"
    assert readiness["limitingLine"]["purchaseId"] == latest["purchaseId"]
    assert readiness["limitingLine"]["fromOrderDeliveryDate"] is True

    orders = {o["supplierName"]: o for o in readiness["supplierOrders"]}
    assert orders["Slow Alloys"]["status"] == "ISSUED"
    assert orders["Slow Alloys"]["poNumber"]
    assert orders["Slow Alloys"]["expectedDeliveryDate"] == "2031-03-10"
    assert orders["Fast Steel"]["poNumber"]
    assert any(o["poNumber"] is None for o in readiness["supplierOrders"])
