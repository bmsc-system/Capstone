"""Admin is view-only on Supplier Orders and Inventory, and may add and edit Clients and Suppliers.

The Admin can open every screen (lists, details, exports, printouts, QR codes)
but every change is refused on the server; Office Staff keep full access.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.catalog_helpers import ensure_material
from app.extensions import db
from app.models.client import Client
from app.models.material_purchase import MaterialPurchase
from app.models.supplier import Supplier
from app.models.tool import Tool, ToolCategory
from app.models.tool_type import ToolType, ToolUnit
from tests.test_material_delay import _draft_order, _headers, _issue, _job, _local, shop  # noqa: F401

X = "00000000-0000-0000-0000-000000000000"

ADMIN_REFUSED = [
    ("POST", "/api/v1/supplier-orders/draft-lines"),
    ("PATCH", f"/api/v1/supplier-orders/{X}"),
    ("PATCH", f"/api/v1/supplier-orders/{X}/expected-delivery"),
    ("POST", f"/api/v1/supplier-orders/{X}/submit"),
    ("POST", f"/api/v1/supplier-orders/{X}/withdraw"),
    ("POST", f"/api/v1/supplier-orders/{X}/cancel"),
    ("POST", f"/api/v1/supplier-orders/{X}/receive"),
    ("POST", f"/api/v1/supplier-orders/{X}/print"),
    ("PATCH", f"/api/v1/supplier-orders/{X}/lines/{X}"),
    ("DELETE", f"/api/v1/supplier-orders/{X}/lines/{X}"),
    ("POST", "/api/v1/tools"),
    ("PATCH", f"/api/v1/tools/{X}"),
    ("POST", f"/api/v1/tools/{X}/adjust"),
    ("POST", "/api/v1/tools/types"),
    ("PATCH", f"/api/v1/tools/types/{X}"),
    ("POST", f"/api/v1/tools/types/{X}/units"),
    ("DELETE", f"/api/v1/tools/types/{X}"),
    ("DELETE", f"/api/v1/tools/units/{X}"),
    ("PATCH", f"/api/v1/tools/units/{X}"),
    ("POST", f"/api/v1/job-orders/{X}/material-purchases"),
    ("PATCH", f"/api/v1/job-orders/{X}/material-purchases/{X}"),
    ("DELETE", f"/api/v1/job-orders/{X}/material-purchases/{X}"),
]


def _inventory(shop):
    consumable = Tool(
        name="Cutting disc",
        code="CD-001",
        category=ToolCategory.CONSUMABLE,
        unit="pcs",
        quantity_on_hand=Decimal("10"),
    )
    tool_type = ToolType(name="Torque wrench", code="TW")
    db.session.add_all([consumable, tool_type])
    db.session.flush()
    unit = ToolUnit(tool_type_id=tool_type.id, asset_code="TW-001")
    db.session.add(unit)
    db.session.commit()
    return consumable, tool_type, unit


@pytest.mark.parametrize("method,url", ADMIN_REFUSED)
def test_admin_is_refused_on_every_change(client, shop, method, url):
    res = client.open(url, method=method, json={}, headers=_headers(shop["admin"]))
    assert res.status_code == 403, (method, url, res.get_json())


def test_admin_refusal_changes_nothing(client, shop):
    consumable, tool_type, unit = _inventory(shop)
    h = _headers(shop["admin"])

    assert (
        client.post(f"/api/v1/tools/{consumable.id}/adjust", json={"quantity": -5, "reason": "x"}, headers=h).status_code
        == 403
    )
    assert (
        client.patch(f"/api/v1/tools/units/{unit.id}", json={"status": "RETIRED"}, headers=h).status_code
        == 403
    )

    db.session.expire_all()
    assert db.session.get(Tool, consumable.id).quantity_on_hand == Decimal("10")
    assert db.session.get(ToolUnit, unit.id).status.value == "AVAILABLE"


def test_admin_can_view_every_screen(client, shop):
    consumable, tool_type, unit = _inventory(shop)
    tomorrow = shop["today"] + timedelta(days=1)
    job, _ = _job(shop, _local(tomorrow, 9))
    order = _draft_order(client, shop, job, shop["slow"])
    _issue(client, shop, order["id"], shop["today"])
    h = _headers(shop["admin"])

    urls = [
        "/api/v1/clients",
        f"/api/v1/clients/{shop['client'].id}",
        "/api/v1/suppliers",
        f"/api/v1/suppliers/{shop['slow'].id}",
        "/api/v1/supplier-orders",
        "/api/v1/supplier-orders/outstanding",
        f"/api/v1/supplier-orders/{order['id']}",
        f"/api/v1/supplier-orders/{order['id']}/print",
        "/api/v1/tools",
        f"/api/v1/tools/{consumable.id}",
        f"/api/v1/tools/{consumable.id}/qr",
        "/api/v1/tools/events",
        "/api/v1/tools/types",
        f"/api/v1/tools/types/{tool_type.id}",
        f"/api/v1/tools/units/{unit.id}/qr",
        "/api/v1/inventory/material-purchases",
        "/api/v1/inventory/purchase-suggestions",
        f"/api/v1/job-orders/{job.id}/material-purchases",
    ]
    for url in urls:
        res = client.get(url, headers=h)
        assert res.status_code == 200, (url, res.status_code)


def test_office_staff_can_still_change_everything(client, shop):
    consumable, tool_type, unit = _inventory(shop)
    h = _headers(shop["office"])

    res = client.post("/api/v1/clients", json={"name": "New Client"}, headers=h)
    assert res.status_code == 201, res.get_json()
    res = client.patch(f"/api/v1/clients/{res.get_json()['id']}", json={"name": "Renamed"}, headers=h)
    assert res.status_code == 200, res.get_json()

    res = client.post("/api/v1/suppliers", json={"name": "New Steel", "typicalLeadTimeDays": 2}, headers=h)
    assert res.status_code == 201, res.get_json()
    res = client.patch(f"/api/v1/suppliers/{res.get_json()['id']}", json={"phone": "0917"}, headers=h)
    assert res.status_code == 200, res.get_json()

    res = client.post("/api/v1/tools", json={"name": "Grinding wheel", "unit": "pcs"}, headers=h)
    assert res.status_code == 201, res.get_json()
    assert client.patch(f"/api/v1/tools/{consumable.id}", json={"minimumStock": 3}, headers=h).status_code == 200
    assert (
        client.post(f"/api/v1/tools/{consumable.id}/adjust", json={"quantity": -2, "reason": "Damaged"}, headers=h).status_code
        in (200, 201)
    )

    res = client.post("/api/v1/tools/types", json={"name": "Caliper"}, headers=h)
    assert res.status_code == 201, res.get_json()
    new_type = res.get_json()["id"]
    assert client.patch(f"/api/v1/tools/types/{new_type}", json={"description": "Digital"}, headers=h).status_code == 200
    assert client.post(f"/api/v1/tools/types/{new_type}/units", json={}, headers=h).status_code == 201
    assert client.patch(f"/api/v1/tools/units/{unit.id}", json={"status": "UNDER_REPAIR"}, headers=h).status_code == 200


def test_office_staff_can_still_run_supplier_orders_and_job_materials(client, shop):
    tomorrow = shop["today"] + timedelta(days=1)
    job, _ = _job(shop, _local(tomorrow, 9))
    h = _headers(shop["office"])

    order = _draft_order(client, shop, job, shop["slow"])
    line_id = order["lines"][0]["id"]
    assert client.patch(f"/api/v1/supplier-orders/{order['id']}", json={"notes": "Rush"}, headers=h).status_code == 200
    res = client.patch(
        f"/api/v1/supplier-orders/{order['id']}/lines/{line_id}",
        json={"quantity": 2, "unitCost": 120},
        headers=h,
    )
    assert res.status_code == 200, res.get_json()
    _issue(client, shop, order["id"], shop["today"])
    res = client.post(f"/api/v1/supplier-orders/{order['id']}/print", headers=h)
    assert res.status_code == 200, res.get_json()
    res = client.post(
        f"/api/v1/supplier-orders/{order['id']}/receive",
        json={"receivedDate": shop["today"].isoformat()},
        headers=h,
    )
    assert res.status_code == 200, res.get_json()

    other = _draft_order(client, shop, job, shop["quick"])
    assert client.post(f"/api/v1/supplier-orders/{other['id']}/cancel", headers=h).status_code == 200

    res = client.post(
        f"/api/v1/job-orders/{job.id}/material-purchases",
        json={"supplierId": shop["quick"].id, "materialCatalogId": ensure_material("Plate"), "materialName": "Plate", "quantity": 4, "unitCost": 5},
        headers=h,
    )
    assert res.status_code == 201, res.get_json()

    legacy = MaterialPurchase(
        job_order_id=job.id,
        material_name="Washers",
        quantity=Decimal("4"),
        unit="pcs",
        unit_cost=Decimal("2"),
        supplier_id=shop["quick"].id,
        date_ordered=shop["today"],
    )
    db.session.add(legacy)
    db.session.commit()
    purchase_id = legacy.id
    res = client.patch(
        f"/api/v1/job-orders/{job.id}/material-purchases/{purchase_id}", json={"quantity": 6}, headers=h
    )
    assert res.status_code == 200, res.get_json()
    res = client.delete(f"/api/v1/job-orders/{job.id}/material-purchases/{purchase_id}", headers=h)
    assert res.status_code in (200, 204), res.get_json()

def test_stocktake_endpoints_removed(client, shop):
    h = _headers(shop["office"])
    assert client.get("/api/v1/inventory/stocktakes", headers=h).status_code == 404
    assert client.get("/api/v1/inventory/stocktakes/form", headers=h).status_code == 404
    assert client.post("/api/v1/inventory/stocktakes", json={"lines": []}, headers=h).status_code == 404



def test_admin_adds_and_edits_clients_and_suppliers(client, shop):
    """Client and supplier records: Admin has the same access as Office Staff."""
    h = _headers(shop["admin"])
    res = client.post("/api/v1/clients", json={"name": "Admin Client"}, headers=h)
    assert res.status_code == 201, res.get_json()
    res = client.patch(f"/api/v1/clients/{res.get_json()['id']}", json={"name": "Renamed"}, headers=h)
    assert res.status_code == 200, res.get_json()
    res = client.post("/api/v1/suppliers", json={"name": "Admin Steel", "typicalLeadTimeDays": 2}, headers=h)
    assert res.status_code == 201, res.get_json()
    res = client.patch(f"/api/v1/suppliers/{res.get_json()['id']}", json={"phone": "0917"}, headers=h)
    assert res.status_code == 200, res.get_json()


def test_admin_cannot_encode_job_orders(client, shop):
    res = client.post(
        "/api/v1/job-orders",
        json={"clientId": shop["client"].id, "title": "Nope", "dueDate": "2031-01-10"},
        headers=_headers(shop["admin"]),
    )
    assert res.status_code == 403
