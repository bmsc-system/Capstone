"""Unified catalog, catalog-backed PO lines and print-before-receive."""

from decimal import Decimal

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.material_catalog import (
    CONSUMABLE_UNITS,
    MATERIAL_CATEGORIES,
    MATERIAL_UNITS,
    MaterialCatalogItem,
)
from app.models.supplier_order import SupplierOrder
from app.models.tool import Tool, ToolCategory
from tests.catalog_helpers import ensure_material
from tests.test_supplier_orders import (  # noqa: F401
    _add_lines,
    _headers,
    _issue,
    _line,
    _print,
    _receive,
    shop,
)

CATALOG = "/api/v1/catalog"


def _tool(name, code, *, qty="0", unit="pcs", active=True):
    tool = Tool(
        name=name,
        code=code,
        category=ToolCategory.CONSUMABLE,
        unit=unit,
        quantity_on_hand=Decimal(qty),
        active=active,
    )
    db.session.add(tool)
    db.session.commit()
    return tool


def _issued_order(client, shop, **line_extra):
    res = _add_lines(client, shop, [_line(shop["job_a"], **line_extra)])
    assert res.status_code == 200, res.get_json()
    order = res.get_json()
    assert _issue(client, shop, order["id"]).status_code == 200
    return order


# --- Catalog list / create -----------------------------------------------------


def test_catalog_lists_materials_and_consumables(client, shop):
    ensure_material("Round bar 50mm")
    stocked = _tool("Cutting disc", "CAT-DISC", qty="5")
    fresh = _tool("Sandpaper", "CAT-SAND")
    _tool("Retired glue", "CAT-GLUE", active=False)
    h = _headers(shop["office"])

    res = client.get(CATALOG, headers=h)
    assert res.status_code == 200
    body = res.get_json()
    by_id = {i["id"]: i for i in body["items"]}
    assert {i["kind"] for i in body["items"]} == {"MATERIAL", "CONSUMABLE"}
    assert body["materialUnits"] == MATERIAL_UNITS
    assert body["consumableUnits"] == CONSUMABLE_UNITS
    assert body["categories"] == MATERIAL_CATEGORIES
    assert by_id[stocked.id]["notStockedYet"] is False
    assert by_id[fresh.id]["notStockedYet"] is True
    assert by_id[fresh.id]["code"] == "CAT-SAND"
    assert "Retired glue" not in {i["name"] for i in body["items"]}
    names = [i["name"].lower() for i in body["items"]]
    assert names == sorted(names)

    only_consumables = client.get(f"{CATALOG}?type=CONSUMABLE", headers=h).get_json()["items"]
    assert only_consumables and all(i["kind"] == "CONSUMABLE" for i in only_consumables)
    only_materials = client.get(f"{CATALOG}?type=MATERIAL", headers=h).get_json()["items"]
    assert only_materials and all(i["kind"] == "MATERIAL" for i in only_materials)
    found = client.get(f"{CATALOG}?search=sandp", headers=h).get_json()["items"]
    assert [i["name"] for i in found] == ["Sandpaper"]
    everything = client.get(f"{CATALOG}?includeInactive=1", headers=h).get_json()["items"]
    assert "Retired glue" in {i["name"] for i in everything}
    assert client.get(f"{CATALOG}?type=bogus", headers=h).status_code == 400


def test_add_catalog_material_and_consumable(client, shop):
    h = _headers(shop["office"])
    res = client.post(
        CATALOG,
        json={
            "kind": "MATERIAL",
            "name": "C93200",
            "category": "Non-ferrous",
            "unit": "pcs",
        },
        headers=h,
    )
    assert res.status_code == 201, res.get_json()
    item = res.get_json()
    assert item["kind"] == "MATERIAL" and item["unit"] == "pcs"
    assert item["gradesOrSizes"] == []

    res = client.post(
        CATALOG,
        json={"kind": "CONSUMABLE", "name": "Masking tape office", "unit": "roll"},
        headers=h,
    )
    assert res.status_code == 201, res.get_json()
    item = res.get_json()
    assert item["kind"] == "CONSUMABLE" and item["notStockedYet"] is True
    assert item["quantityOnHand"] == 0 and item["active"] is True
    assert db.session.get(Tool, item["id"]).quantity_on_hand == 0

    # Admin views catalog but cannot add items.
    denied = client.post(
        CATALOG,
        json={"kind": "CONSUMABLE", "name": "Admin tape", "unit": "roll"},
        headers=_headers(shop["admin"]),
    )
    assert denied.status_code == 403


def test_catalog_consumable_rejects_stock_and_bad_unit(client, shop):
    h = _headers(shop["office"])
    res = client.post(
        CATALOG,
        json={"kind": "CONSUMABLE", "name": "Gloves", "unit": "pair", "quantityOnHand": 5},
        headers=h,
    )
    assert res.status_code == 400
    assert "purchase order" in res.get_json()["error"]["message"]
    assert Tool.query.filter_by(name="Gloves").first() is None

    res = client.post(
        CATALOG, json={"kind": "CONSUMABLE", "name": "Gloves", "unit": "kg"}, headers=h
    )
    assert res.status_code == 400
    res = client.post(CATALOG, json={"kind": "WIDGET", "name": "x"}, headers=h)
    assert res.status_code == 400
    # The plain tools endpoint holds the same rule.
    res = client.post(
        "/api/v1/tools", json={"name": "Gloves", "quantityOnHand": 2}, headers=h
    )
    assert res.status_code == 400


def test_catalog_patch_and_legacy_unit_rules(client, shop):
    h = _headers(shop["office"])
    legacy = _tool("Old pail item", "CAT-LEG", unit="kg")  # not an allowed consumable unit

    # Other fields can change without touching the legacy unit.
    res = client.patch(f"/api/v1/tools/{legacy.id}", json={"shopTerm": "pail"}, headers=h)
    assert res.status_code == 200
    assert res.get_json()["unit"] == "kg"
    # Re-sending the same legacy unit is fine; switching to a new bad unit is not.
    assert client.patch(f"/api/v1/tools/{legacy.id}", json={"unit": "kg"}, headers=h).status_code == 200
    assert client.patch(f"/api/v1/tools/{legacy.id}", json={"unit": "ton"}, headers=h).status_code == 400
    assert client.patch(f"/api/v1/tools/{legacy.id}", json={"unit": "box"}, headers=h).status_code == 200

    res = client.patch(
        f"{CATALOG}/consumables/{legacy.id}", json={"active": False}, headers=h
    )
    assert res.status_code == 200 and res.get_json()["active"] is False
    assert (
        client.patch(
            f"{CATALOG}/consumables/{legacy.id}",
            json={"active": True},
            headers=_headers(shop["admin"]),
        ).status_code
        == 403
    )
    res = client.patch(
        f"{CATALOG}/consumables/{legacy.id}", json={"quantityOnHand": 4}, headers=h
    )
    assert res.status_code == 400

    mat = ensure_material("Plate 6mm")
    res = client.patch(f"{CATALOG}/materials/{mat}", json={"active": False}, headers=h)
    assert res.status_code == 200 and res.get_json()["active"] is False


def test_tools_list_stocked_only(client, shop):
    h = _headers(shop["office"])
    with_stock = _tool("Stocked item", "SO-1", qty="3")
    never = _tool("Never stocked", "SO-2")
    ordered = _tool("On a PO", "SO-3")
    res = _add_lines(client, shop, [{"toolId": ordered.id, "quantity": 2, "unitCost": 5}])
    assert res.status_code == 200, res.get_json()

    everything = {t["id"] for t in client.get("/api/v1/tools", headers=h).get_json()}
    assert {with_stock.id, never.id, ordered.id} <= everything
    stocked = {t["id"] for t in client.get("/api/v1/tools?stockedOnly=1", headers=h).get_json()}
    assert with_stock.id in stocked and ordered.id in stocked
    assert never.id not in stocked
    assert db.session.get(Tool, never.id).is_stocked is False
    assert db.session.get(Tool, ordered.id).is_stocked is True


# --- PO lines -------------------------------------------------------------------


def test_po_line_requires_active_catalog_material(client, shop):
    job = shop["job_a"]

    res = _add_lines(client, shop, [{"jobOrderId": job.id, "materialName": "Typed", "quantity": 1}])
    assert res.status_code == 400
    assert res.get_json()["error"]["message"] == "Pick a material from the catalog."

    inactive = ensure_material("Retired steel", active=False)
    res = _add_lines(
        client, shop, [{"jobOrderId": job.id, "materialCatalogId": inactive, "quantity": 1}]
    )
    assert res.status_code == 400
    assert res.get_json()["error"]["message"] == "Pick a material from the catalog."
    assert SupplierOrder.query.count() == 0

    item = MaterialCatalogItem(name="Shaft steel", default_unit="m", grades=["1045"], active=True)
    db.session.add(item)
    db.session.commit()
    res = _add_lines(
        client,
        shop,
        [
            {
                "jobOrderId": job.id,
                "materialCatalogId": item.id,
                "materialName": "ignored typed text",
                "gradeOrSpec": "1045",
                "sizeSpec": "50 mm x 1 m",
                "quantity": 2,
                "unitCost": 100,
            }
        ],
    )
    assert res.status_code == 200, res.get_json()
    [line] = res.get_json()["lines"]
    assert line["materialName"] == "Shaft steel"
    assert line["materialCatalogId"] == item.id
    assert line["gradeOrSpec"] == "1045" and line["sizeSpec"] == "50 mm x 1 m"
    assert line["unit"] == "m"  # catalog default when none is sent

    res = _add_lines(
        client,
        shop,
        [{"jobOrderId": job.id, "materialCatalogId": item.id, "quantity": 1, "unit": "bucket"}],
    )
    assert res.status_code == 400


def test_consumable_line_needs_offered_tool_but_not_stock(client, shop):
    fresh = _tool("Fresh item", "PO-FRESH", unit="box")
    hidden = _tool("Not offered", "PO-HIDDEN", active=False)

    res = _add_lines(client, shop, [{"toolId": hidden.id, "quantity": 1}])
    assert res.status_code == 400

    res = _add_lines(client, shop, [{"toolId": fresh.id, "quantity": 3, "unit": "crate"}])
    assert res.status_code == 400  # only the listed consumable units
    res = _add_lines(client, shop, [{"toolId": fresh.id, "quantity": 3, "unitCost": 10}])
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["lines"][0]["unit"] == "box"

    ctx = client.get("/api/v1/supplier-orders/outstanding", headers=_headers(shop["office"])).get_json()
    offered = {c["toolId"] for c in ctx["offeredConsumables"]}
    assert fresh.id in offered and hidden.id not in offered
    assert ctx["consumableUnits"] == CONSUMABLE_UNITS and ctx["materialUnits"] == MATERIAL_UNITS


def test_draft_follows_catalog_name_issued_keeps_snapshot(client, shop):
    h = _headers(shop["office"])
    item = MaterialCatalogItem(name="Old name bar", default_unit="pcs", grades=[], active=True)
    db.session.add(item)
    db.session.commit()
    res = _add_lines(
        client, shop, [{"jobOrderId": shop["job_a"].id, "materialCatalogId": item.id, "quantity": 1}]
    )
    order = res.get_json()
    line_id = order["lines"][0]["id"]
    assert order["lines"][0]["materialName"] == "Old name bar"

    # Draft: a catalog rename shows up when the line is next edited.
    assert client.patch(f"/api/v1/material-catalog/{item.id}", json={"name": "Draft name bar"}, headers=h).status_code == 200
    res = client.patch(
        f"/api/v1/supplier-orders/{order['id']}/lines/{line_id}",
        json={"sizeSpec": "20 mm"},
        headers=h,
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["lines"][0]["materialName"] == "Draft name bar"
    assert res.get_json()["lines"][0]["sizeSpec"] == "20 mm"

    # Issue freezes the current catalog name; later renames never touch it.
    assert client.patch(f"/api/v1/material-catalog/{item.id}", json={"name": "Issue name bar"}, headers=h).status_code == 200
    assert _issue(client, shop, order["id"]).status_code == 200
    assert client.patch(f"/api/v1/material-catalog/{item.id}", json={"name": "Renamed later"}, headers=h).status_code == 200
    got = client.get(f"/api/v1/supplier-orders/{order['id']}", headers=h).get_json()
    assert got["lines"][0]["materialName"] == "Issue name bar"
    printed = client.get(f"/api/v1/supplier-orders/{order['id']}/print", headers=h).get_json()
    assert printed["rows"][0]["materialName"] == "Issue name bar"
    assert printed["rows"][0]["sizeSpec"] == "20 mm"
    db.session.expire_all()
    assert db.session.get(MaterialCatalogItem, item.id).name == "Renamed later"


# --- Print before receive ---------------------------------------------------------


def test_print_records_first_print_and_counts_every_print(client, shop):
    h = _headers(shop["office"])
    draft = _add_lines(client, shop, [_line(shop["job_a"])]).get_json()
    assert client.post(f"/api/v1/supplier-orders/{draft['id']}/print", headers=h).status_code == 409
    assert db.session.get(SupplierOrder, draft["id"]).printed_at is None

    assert _issue(client, shop, draft["id"]).status_code == 200
    before = client.get(f"/api/v1/supplier-orders/{draft['id']}", headers=h).get_json()
    assert before["printedAt"] is None and before["printCount"] == 0

    # GET only reads.
    assert client.get(f"/api/v1/supplier-orders/{draft['id']}/print", headers=h).status_code == 200
    assert db.session.get(SupplierOrder, draft["id"]).print_count == 0

    res = client.post(f"/api/v1/supplier-orders/{draft['id']}/print", headers=h)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert data["rows"] and data["supplier"]["name"] == "PO Steel"
    first = data["order"]
    assert first["printedAt"] and first["printCount"] == 1
    assert first["printedById"] == shop["office"].id
    assert first["printedByName"] == shop["office"].full_name

    res = client.post(f"/api/v1/supplier-orders/{draft['id']}/mark-printed", headers=h)
    assert res.status_code == 200
    second = res.get_json()["order"]
    assert second["printCount"] == 2
    assert second["printedAt"] == first["printedAt"]

    events = AuditLog.query.filter_by(
        action="SUPPLIER_ORDER_PRINTED", entity_id=draft["id"]
    ).count()
    assert events == 2


def test_receive_needs_a_print_first(client, shop):
    h = _headers(shop["office"])
    order = _issued_order(client, shop)

    res = _receive(client, shop, order["id"], "2031-03-05", print_first=False)
    assert res.status_code == 409
    assert res.get_json()["error"]["message"] == (
        "Print the PO first \u2014 the supplier needs the printed copy."
    )
    db.session.expire_all()
    assert db.session.get(SupplierOrder, order["id"]).status.value == "ISSUED"

    assert _print(client, shop, order["id"]).status_code == 200
    res = _receive(client, shop, order["id"], "2031-03-05", print_first=False)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "RECEIVED"
    # Already received: refused for that reason, not for the print rule.
    res = _receive(client, shop, order["id"], "2031-03-05", print_first=False)
    assert res.status_code == 409 and res.get_json()["error"]["code"] == "ALREADY_RECEIVED"
    assert client.get(f"/api/v1/supplier-orders/{order['id']}", headers=h).status_code == 200


def test_receiving_a_not_stocked_consumable_makes_it_stocked(client, shop):
    h = _headers(shop["office"])
    created = client.post(
        CATALOG, json={"kind": "CONSUMABLE", "name": "Weld rod", "unit": "box"}, headers=h
    ).get_json()
    tool_id = created["id"]
    stocked_ids = lambda: {  # noqa: E731
        t["id"] for t in client.get("/api/v1/tools?stockedOnly=1", headers=h).get_json()
    }
    assert tool_id not in stocked_ids()

    res = _add_lines(client, shop, [{"toolId": tool_id, "quantity": 12, "unitCost": 80}])
    assert res.status_code == 200, res.get_json()
    order = res.get_json()
    assert _issue(client, shop, order["id"]).status_code == 200
    assert _receive(client, shop, order["id"], "2031-03-05").status_code == 200

    db.session.expire_all()
    tool = db.session.get(Tool, tool_id)
    assert tool.quantity_on_hand == Decimal("12") and tool.is_stocked
    assert tool_id in stocked_ids()
    item = next(
        i for i in client.get(CATALOG, headers=h).get_json()["items"] if i["id"] == tool_id
    )
    assert item["notStockedYet"] is False
