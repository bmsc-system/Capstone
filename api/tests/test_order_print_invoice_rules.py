"""A PO prints once issued, the sales invoice is a recorded reference to the
shop's BIR-registered invoice, and only the Admin sets a job For Delivery.

Uses the bmsc_test database from conftest (schema built from the models).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from tests.catalog_helpers import approve_issue, ensure_material
from app.extensions import bcrypt, db
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.material_purchase import MaterialPurchase
from app.models.operation import JobOperation, OperationStatus
from app.models.sales_invoice import SalesInvoice
from app.models.supplier import Supplier
from app.models.user import User, UserRole, UserStatus
from app.services.schedule_calendar import shop_now


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
    s = {
        "admin": _user("opi_admin@test.local", UserRole.ADMIN),
        "office": _user("opi_office@test.local", UserRole.OFFICE_STAFF),
        "client": Client(name="OPI Client"),
        "steel": Supplier(name="OPI Steel", code="OPS", typical_lead_time_days=5),
    }
    db.session.add_all([s["client"], s["steel"]])
    db.session.commit()
    return s


def _job(shop, *, status=JobOrderStatus.DRAFT, to_order=False, amount=None):
    job = JobOrder(
        client_id=shop["client"].id,
        title="OPI Shaft",
        due_date=date(2031, 6, 1),
        status=status,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        material_status=MaterialStatus.TO_ORDER if to_order else MaterialStatus.NOT_REQUIRED,
        amount=amount,
        created_by_id=shop["office"].id,
    )
    db.session.add(job)
    db.session.commit()
    return job


def _completed_job(shop, amount=Decimal("15000.00")):
    job = _job(shop, status=JobOrderStatus.COMPLETED, amount=amount)
    db.session.add(
        JobOperation(
            job_order_id=job.id,
            sequence_no=1,
            operation_name="Turning",
            status=OperationStatus.COMPLETED,
            estimated_hours=Decimal("1"),
        )
    )
    db.session.commit()
    return job


def _add_lines(client, shop, lines):
    return client.post(
        "/api/v1/supplier-orders/draft-lines",
        json={"supplierId": shop["steel"].id, "lines": lines},
        headers=_headers(shop["office"]),
    )


def _today():
    return shop_now().date().isoformat()


# --- 3. A PO prints only once issued ---


def test_draft_order_cannot_be_printed_until_issued(client, shop):
    job = _job(shop, to_order=True)
    order = _add_lines(
        client,
        shop,
        [
            {
                "jobOrderId": job.id,
                "materialCatalogId": ensure_material("Round bar 50mm"), "materialName": "Round bar 50mm",
                "quantity": 4,
                "unitCost": 250,
            }
        ],
    ).get_json()
    headers = _headers(shop["office"])

    res = client.get(f"/api/v1/supplier-orders/{order['id']}/print", headers=headers)
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "ORDER_NOT_ISSUED"
    assert res.get_json()["error"]["message"] == "Issue the order before printing."

    res = approve_issue(client, f"/api/v1/supplier-orders/{order['id']}", {"dateIssued": "2031-03-02"}, headers)
    assert res.status_code == 200, res.get_json()

    res = client.get(f"/api/v1/supplier-orders/{order['id']}/print", headers=headers)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["order"]["poNumber"] == "OPS31000001"


def _issued_order(client, shop, job=None):
    job = job or _job(shop, to_order=True)
    order = _add_lines(
        client,
        shop,
        [{"jobOrderId": job.id, "materialCatalogId": ensure_material("Round bar 50mm"), "materialName": "Round bar 50mm", "quantity": 4, "unitCost": 250}],
    ).get_json()
    res = approve_issue(client, f"/api/v1/supplier-orders/{order['id']}", {"dateIssued": "2031-03-02"}, _headers(shop["office"]))
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def test_terms_and_delivery_mode_default_and_stay_editable_after_issue(client, shop):
    order = _issued_order(client, shop)
    assert order["termsOfPayment"] == "PDC"
    assert order["deliveryMode"] == "DELIVERY"
    headers = _headers(shop["office"])

    res = client.patch(
        f"/api/v1/supplier-orders/{order['id']}",
        json={"termsOfPayment": "30 days", "deliveryMode": "pickup"},
        headers=headers,
    )
    assert res.status_code == 200, res.get_json()
    printed = client.get(f"/api/v1/supplier-orders/{order['id']}/print", headers=headers).get_json()
    assert printed["order"]["termsOfPayment"] == "30 days"
    assert printed["order"]["deliveryMode"] == "PICKUP"

    # Notes and VAT stay locked after issue.
    res = client.patch(
        f"/api/v1/supplier-orders/{order['id']}", json={"vatRate": 12}, headers=headers
    )
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "ORDER_LOCKED"


def test_delivery_mode_and_terms_are_validated(client, shop):
    order = _issued_order(client, shop)
    headers = _headers(shop["office"])
    res = client.patch(
        f"/api/v1/supplier-orders/{order['id']}", json={"deliveryMode": "COURIER"}, headers=headers
    )
    assert res.status_code == 400
    res = client.patch(
        f"/api/v1/supplier-orders/{order['id']}", json={"termsOfPayment": "  "}, headers=headers
    )
    assert res.status_code == 400


def test_cancelled_order_cannot_be_printed(client, shop):
    order = _issued_order(client, shop)
    headers = _headers(shop["office"])
    res = client.post(f"/api/v1/supplier-orders/{order['id']}/cancel", headers=headers)
    assert res.status_code == 200, res.get_json()
    res = client.get(f"/api/v1/supplier-orders/{order['id']}/print", headers=headers)
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "ORDER_CANCELLED"
    res = client.patch(
        f"/api/v1/supplier-orders/{order['id']}", json={"termsOfPayment": "COD"}, headers=headers
    )
    assert res.status_code == 409


def test_job_material_lines_name_the_supplier(client, shop):
    job = _job(shop, to_order=True)
    _issued_order(client, shop, job)
    res = client.get(f"/api/v1/job-orders/{job.id}", headers=_headers(shop["admin"]))
    assert res.status_code == 200, res.get_json()
    lines = res.get_json()["materialLines"]
    assert [ln["supplierName"] for ln in lines] == ["OPI Steel"]
    assert lines[0]["poNumber"] == "OPS31000001"


# --- 4. Sales invoice: a recorded reference ---


def _record(client, shop, job, user=None, **body):
    payload = {"invoiceNumber": "SI-0001234", "invoiceDate": _today(), **body}
    return client.post(
        f"/api/v1/job-orders/{job.id}/invoice",
        json=payload,
        headers=_headers(user or shop["office"]),
    )


def test_office_records_the_bir_invoice_number_and_amount_defaults_to_job_amount(client, shop):
    job = _completed_job(shop)

    res = _record(client, shop, job)
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    assert body["invoiceNumber"] == "SI-0001234"
    assert body["invoiceDate"] == _today()
    assert body["amount"] == 15000.0
    assert body["preparedById"] == shop["office"].id

    inv = SalesInvoice.query.filter_by(job_order_id=job.id).one()
    assert inv.invoice_seq is None
    assert not inv.invoice_number.startswith("BMSC-INV-")


def test_entered_amount_overrides_the_job_amount(client, shop):
    job = _completed_job(shop)
    res = _record(client, shop, job, amount=12345.5)
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["amount"] == 12345.5


def test_admin_cannot_record_an_invoice(client, shop):
    job = _completed_job(shop)
    res = _record(client, shop, job, user=shop["admin"])
    assert res.status_code == 403
    assert SalesInvoice.query.count() == 0


@pytest.mark.parametrize(
    "body, field",
    [
        ({"invoiceNumber": "   "}, "invoiceNumber"),
        ({"invoiceNumber": "X" * 65}, "invoiceNumber"),
        ({"invoiceDate": None}, "invoiceDate"),
        ({"invoiceDate": "2999-01-01"}, "invoiceDate"),
        ({"amount": -1}, "amount"),
    ],
)
def test_invoice_entry_is_validated(client, shop, body, field):
    job = _completed_job(shop)
    res = _record(client, shop, job, **body)
    assert res.status_code == 400, res.get_json()
    assert field in res.get_json()["error"]["message"]


def test_invoice_number_cannot_be_reused_on_another_job(client, shop):
    assert _record(client, shop, _completed_job(shop)).status_code == 201
    res = _record(client, shop, _completed_job(shop), invoiceNumber="si-0001234")
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "DUPLICATE_INVOICE_NUMBER"


def test_invoice_needs_a_completed_job_and_only_one_per_job(client, shop):
    open_job = _job(shop, status=JobOrderStatus.IN_PROGRESS, amount=Decimal("100"))
    res = _record(client, shop, open_job)
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "INVALID_TRANSITION"

    job = _completed_job(shop)
    assert _record(client, shop, job).status_code == 201
    res = _record(client, shop, job, invoiceNumber="SI-0009999")
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "INVOICE_EXISTS"


def _deliver(client, job, user):
    """Set For Delivery (the step that needs the invoice)."""
    return client.post(f"/api/v1/job-orders/{job.id}/for-delivery", headers=_headers(user))


def _delivered(client, job, user, **body):
    return client.post(f"/api/v1/job-orders/{job.id}/deliver", headers=_headers(user), json=body)


def test_for_delivery_requires_a_recorded_invoice(client, shop):
    job = _completed_job(shop)

    res = _deliver(client, job, shop["admin"])
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "INVOICE_REQUIRED"
    assert "sales invoice" in res.get_json()["error"]["message"]

    assert _record(client, shop, job).status_code == 201
    res = _deliver(client, job, shop["admin"])
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["status"] == "FOR_DELIVERY"
    assert body["forDeliveryAt"]
    assert body["deliveredAt"] is None


def test_mark_delivered_after_for_delivery_with_a_date(client, shop):
    from datetime import timedelta

    from app.services.schedule_calendar import shop_now

    job = _completed_job(shop)
    assert _record(client, shop, job).status_code == 201

    res = _delivered(client, job, shop["admin"])
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "INVALID_TRANSITION"

    assert _deliver(client, job, shop["admin"]).status_code == 200
    assert _delivered(client, job, shop["office"]).status_code == 403

    tomorrow = (shop_now().date() + timedelta(days=1)).isoformat()
    res = _delivered(client, job, shop["admin"], deliveredDate=tomorrow)
    assert res.status_code == 400

    yesterday = (shop_now().date() - timedelta(days=1)).isoformat()
    res = _delivered(client, job, shop["admin"], deliveredDate=yesterday)
    assert res.status_code == 400, "cannot be before it was set For Delivery"

    res = _delivered(client, job, shop["admin"], deliveredDate=shop_now().date().isoformat())
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["status"] == "DELIVERED"
    assert body["deliveredAt"]


def test_office_staff_can_no_longer_set_for_delivery(client, shop):
    job = _completed_job(shop)
    assert _record(client, shop, job).status_code == 201
    res = _deliver(client, job, shop["office"])
    assert res.status_code == 403
    db.session.expire_all()
    assert db.session.get(JobOrder, job.id).status == JobOrderStatus.COMPLETED


def test_for_delivery_needs_a_completed_job(client, shop):
    job = _job(shop, status=JobOrderStatus.IN_PROGRESS, amount=Decimal("100"))
    res = _deliver(client, job, shop["admin"])
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "INVALID_TRANSITION"
    assert "Completed" in res.get_json()["error"]["message"]


def _correct(client, shop, job, user=None, **body):
    return client.patch(
        f"/api/v1/job-orders/{job.id}/invoice",
        json=body,
        headers=_headers(user or shop["office"]),
    )


def test_correction_needs_a_reason_and_logs_old_and_new_values(client, shop):
    job = _completed_job(shop)
    assert _record(client, shop, job).status_code == 201
    yesterday = (shop_now().date() - timedelta(days=1)).isoformat()

    res = _correct(client, shop, job, invoiceNumber="SI-0001243", amount=14000)
    assert res.status_code == 400
    assert "reason" in res.get_json()["error"]["message"]

    res = _correct(
        client,
        shop,
        job,
        invoiceNumber="SI-0001243",
        invoiceDate=yesterday,
        amount=14000,
        reason="Digits swapped when typing",
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["invoiceNumber"] == "SI-0001243"
    assert body["invoiceDate"] == yesterday
    assert body["amount"] == 14000.0

    inv = SalesInvoice.query.filter_by(job_order_id=job.id).one()
    row = AuditLog.query.filter_by(
        action="SALES_INVOICE_CORRECTED", entity_type="SalesInvoice", entity_id=inv.id
    ).one()
    assert row.user_id == shop["office"].id
    assert row.user_role == UserRole.OFFICE_STAFF.value
    assert row.before_json == {"invoiceNumber": "SI-0001234", "invoiceDate": _today(), "amount": 15000.0}
    assert row.after_json == {
        "invoiceNumber": "SI-0001243",
        "invoiceDate": yesterday,
        "amount": 14000.0,
        "reason": "Digits swapped when typing",
    }


def test_correction_with_no_change_is_refused(client, shop):
    job = _completed_job(shop)
    assert _record(client, shop, job).status_code == 201
    res = _correct(client, shop, job, invoiceNumber="SI-0001234", reason="Checking")
    assert res.status_code == 400


def test_admin_cannot_correct_an_invoice(client, shop):
    job = _completed_job(shop)
    assert _record(client, shop, job).status_code == 201
    res = _correct(client, shop, job, user=shop["admin"], amount=1, reason="x")
    assert res.status_code == 403


def test_invoice_is_locked_after_delivery(client, shop):
    job = _completed_job(shop)
    assert _record(client, shop, job).status_code == 201
    assert _deliver(client, job, shop["admin"]).status_code == 200

    res = _correct(client, shop, job, amount=1, reason="Too late")
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "INVOICE_LOCKED"
    assert SalesInvoice.query.filter_by(job_order_id=job.id).one().total == Decimal("15000.00")


def test_existing_generated_invoice_numbers_stay_as_references(client, shop):
    job = _completed_job(shop)
    db.session.add(
        SalesInvoice(
            invoice_seq=7,
            invoice_number="BMSC-INV-00007",
            invoice_date=date(2026, 9, 1),
            job_order_id=job.id,
            client_id=job.client_id,
            description="Old generated invoice",
            subtotal=Decimal("10000"),
            vat_rate=Decimal("12"),
            vat_amount=Decimal("1200"),
            total=Decimal("11200"),
            prepared_by_id=shop["office"].id,
        )
    )
    db.session.commit()

    body = client.get(f"/api/v1/job-orders/{job.id}/invoice", headers=_headers(shop["office"])).get_json()
    assert body["invoiceNumber"] == "BMSC-INV-00007"
    assert body["amount"] == 11200.0

    res = _deliver(client, job, shop["admin"])
    assert res.status_code == 200, res.get_json()
