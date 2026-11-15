"""Consumable restock lines on supplier orders, alongside job materials."""

from datetime import date
from decimal import Decimal

import pytest

from tests.catalog_helpers import ensure_material
from app.extensions import db
from app.models.tool import Tool, ToolCategory
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.user import UserRole
from app.services.supplier_reliability_service import supplier_reliability
from tests.test_supplier_orders import _add_lines, _headers, _issue, _job, _line, _user


@pytest.fixture
def shop(app):
    from app.models.client import Client
    from app.models.supplier import Supplier

    admin = _user("cpo_admin@test.local", UserRole.ADMIN)
    office = _user("cpo_office@test.local", UserRole.OFFICE_STAFF)
    client_row = Client(name="CPO Client")
    hardware = Supplier(name="CPO Hardware", code="CPH", typical_lead_time_days=2)
    disc = Tool(
        name="Cutting disc",
        code="CPO-DISC",
        category=ToolCategory.CONSUMABLE,
        unit="pcs",
        quantity_on_hand=Decimal("3"),
        minimum_stock=Decimal("10"),
        size_spec='4"',
        ordered_from_supplier=True,
    )
    db.session.add_all([client_row, hardware, disc])
    db.session.commit()
    s = {"admin": admin, "office": office, "client": client_row, "hardware": hardware, "disc": disc}
    s["job"] = _job(s, "Shaft")
    return s


def _consumable_line(shop, quantity=20):
    return {"toolId": shop["disc"].id, "quantity": quantity, "unit": "pcs", "unitCost": 35}


def _receive(client, shop, order_id, when="2031-03-04", user=None):
    client.post(
        f"/api/v1/supplier-orders/{order_id}/print", headers=_headers(user or shop["office"])
    )
    return client.post(
        f"/api/v1/supplier-orders/{order_id}/receive",
        json={"receivedDate": when},
        headers=_headers(user or shop["office"]),
    )


def test_due_consumables_are_offered_with_the_usual_quantity(client, shop):
    """Bought every ~10 days, last bought 12 days ago: offered on a new supplier order."""
    from datetime import timedelta

    from app.models.consumable_purchase import ConsumablePurchase
    from app.services.schedule_calendar import shop_now

    disc = shop["disc"]
    today = shop_now().date()
    for days_ago, qty in ((42, 20), (32, 18), (22, 22), (12, 20)):
        db.session.add(
            ConsumablePurchase(
                tool_id=disc.id,
                purchased_on=today - timedelta(days=days_ago),
                quantity=Decimal(qty),
                unit="pcs",
                unit_cost=Decimal("35"),
                recorded_by_id=shop["office"].id,
            )
        )
    db.session.commit()

    res = client.get("/api/v1/supplier-orders/outstanding", headers=_headers(shop["office"]))
    assert res.status_code == 200, res.get_json()
    rows = res.get_json()["dueConsumables"]
    row = next((r for r in rows if r["toolId"] == disc.id), None)
    assert row is not None, f"expected disc in {rows}"
    assert row["status"] == "OVERDUE"
    assert 18 <= row["usualQuantity"] <= 22
    assert row["remainingSuggestedQuantity"] == row["usualQuantity"]


def test_one_order_holds_job_materials_and_consumables(client, shop):
    res = _add_lines(
        client,
        shop,
        [_line(shop["job"]), _consumable_line(shop)],
        supplier=shop["hardware"],
    )
    assert res.status_code == 200, res.get_json()
    order = res.get_json()
    kinds = {ln["kind"] for ln in order["lines"]}
    assert kinds == {"JOB_MATERIAL", "CONSUMABLE"}
    assert order["jobCount"] == 1 and order["consumableLineCount"] == 1

    _issue(client, shop, order["id"])
    printed = client.get(
        f"/api/v1/supplier-orders/{order['id']}/print", headers=_headers(shop["admin"])
    ).get_json()
    consumable_rows = [r for r in printed["rows"] if r["isConsumable"]]
    assert len(consumable_rows) == 1
    assert all("jobNumbers" not in r for r in printed["rows"])


def test_consumable_line_received_through_po_adds_stock_once(client, shop):
    order = _add_lines(client, shop, [_consumable_line(shop, 20)], supplier=shop["hardware"]).get_json()
    _issue(client, shop, order["id"], when="2031-03-02")
    line_id = order["lines"][0]["id"]

    res = _receive(client, shop, order["id"])
    assert res.status_code == 200, res.get_json()
    db.session.expire_all()
    assert Decimal(str(db.session.get(Tool, shop["disc"].id).quantity_on_hand)) == Decimal("23")
    events = ToolEvent.query.filter_by(tool_id=shop["disc"].id, type=ToolEventType.RECEIVE).all()
    assert len(events) == 1
    assert events[0].material_purchase_id == line_id
    assert events[0].to_dict()["poNumber"] == res.get_json()["poNumber"]

    again = _receive(client, shop, order["id"])
    assert again.status_code == 409

    from app.models.material_purchase import MaterialPurchase
    from app.services import material_purchase_service
    from app.utils.errors import AppError

    with pytest.raises(AppError) as exc:
        material_purchase_service.receive_lines(
            [db.session.get(MaterialPurchase, line_id)], actor_id=shop["office"].id
        )
    assert exc.value.code == "ALREADY_RECEIVED"
    db.session.expire_all()
    assert Decimal(str(db.session.get(Tool, shop["disc"].id).quantity_on_hand)) == Decimal("23")


def test_job_material_line_is_typed_without_a_planned_material(client, shop):
    res = _add_lines(
        client,
        shop,
        [{"jobOrderId": shop["job"].id, "materialCatalogId": ensure_material("Plate 10mm"), "materialName": "Plate 10mm", "quantity": 1, "unit": "pcs"}],
        supplier=shop["hardware"],
    )
    assert res.status_code == 200, res.get_json()
    [line] = res.get_json()["lines"]
    assert line["materialName"] == "Plate 10mm" and line["plannedMaterialId"] is None

    res = _add_lines(
        client, shop, [{"jobOrderId": shop["job"].id, "quantity": 1}], supplier=shop["hardware"]
    )
    assert res.status_code == 400


def test_a_line_cannot_be_both_job_material_and_consumable(client, shop):
    line = {**_consumable_line(shop), "jobOrderId": shop["job"].id}
    res = _add_lines(client, shop, [line], supplier=shop["hardware"])
    assert res.status_code == 400


def test_reliability_counts_consumable_deliveries(client, shop):
    received = ["2031-03-04", "2031-03-11", "2031-03-25"]
    issued = ["2031-03-02", "2031-03-09", "2031-03-16"]
    for when_issued, when_received in zip(issued, received):
        order = _add_lines(client, shop, [_consumable_line(shop, 5)], supplier=shop["hardware"]).get_json()
        assert _issue(client, shop, order["id"], when=when_issued).status_code == 200
        assert _receive(client, shop, order["id"], when=when_received).status_code == 200

    row = next(
        r for r in supplier_reliability(today=date(2031, 4, 1)) if r["supplierId"] == shop["hardware"].id
    )
    assert row["dueDeliveries"] == 3
    assert row["onTimeDeliveries"] == 2 and row["lateDeliveries"] == 1
    assert row["enoughData"] is True


def test_admin_cannot_add_or_receive_consumable_lines(client, shop):
    res = _add_lines(client, shop, [_consumable_line(shop)], supplier=shop["hardware"], user=shop["admin"])
    assert res.status_code == 403

    order = _add_lines(client, shop, [_consumable_line(shop)], supplier=shop["hardware"]).get_json()
    _issue(client, shop, order["id"])
    res = _receive(client, shop, order["id"], user=shop["admin"])
    assert res.status_code == 403
    db.session.expire_all()
    assert Decimal(str(db.session.get(Tool, shop["disc"].id).quantity_on_hand)) == Decimal("3")

    view = client.get(f"/api/v1/supplier-orders/{order['id']}", headers=_headers(shop["admin"]))
    assert view.status_code == 200


def test_store_bought_consumables_stay_off_supplier_orders(client, shop):
    """Most consumables are bought in cash at a store; only those marked
    Ordered from a supplier go on a supplier order or its due list."""
    from datetime import timedelta

    from app.models.consumable_purchase import ConsumablePurchase
    from app.services.schedule_calendar import shop_now

    tape = Tool(name="Masking tape", code="CPO-TAPE", category=ToolCategory.CONSUMABLE, unit="roll")
    db.session.add(tape)
    db.session.flush()
    today = shop_now().date()
    for days_ago in (42, 32, 22, 12):
        db.session.add(
            ConsumablePurchase(
                tool_id=tape.id, purchased_on=today - timedelta(days=days_ago), quantity=Decimal(5),
                unit="roll", recorded_by_id=shop["office"].id,
            )
        )
    db.session.commit()

    due = client.get("/api/v1/supplier-orders/outstanding", headers=_headers(shop["office"])).get_json()
    assert tape.id not in {r["toolId"] for r in due["dueConsumables"]}
    res = _add_lines(
        client, shop, [{"toolId": tape.id, "quantity": 5, "unit": "roll", "unitCost": 40}],
        supplier=shop["hardware"],
    )
    assert res.status_code == 400
    assert "not ordered from a supplier" in res.get_json()["error"]["message"]
