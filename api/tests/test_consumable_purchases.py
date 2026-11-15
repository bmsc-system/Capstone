"""Consumable purchases and the replenishment forecast (Croston's method, SBA)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.consumable_purchase import ConsumablePurchase
from app.models.tool import Tool, ToolCategory
from app.models.user import UserRole
from app.services import replenishment_service
from app.services.replenishment_service import croston_sba
from app.services.schedule_calendar import shop_now
from tests.test_material_delay import _headers, _user

URL = "/api/v1/consumables/purchases"


@pytest.fixture
def shop(app):
    office = _user("cp_office@test.local", UserRole.OFFICE_STAFF)
    admin = _user("cp_admin@test.local", UserRole.ADMIN)
    worker = _user("cp_worker@test.local", UserRole.PRODUCTION_WORKER)
    oil = Tool(code="CP-OIL", name="Cutting oil", category=ToolCategory.CONSUMABLE, unit="gal")
    db.session.add(oil)
    db.session.commit()
    return {"office": office, "admin": admin, "worker": worker, "oil": oil}


def test_croston_sba_smooths_size_and_interval():
    d0 = date(2031, 1, 1)
    events = [(d0, 10), (d0 + timedelta(days=10), 20), (d0 + timedelta(days=30), 10)]
    fit = croston_sba(events, alpha=0.2)
    # Size: 10 -> 12 -> 11.6. Interval: 10 -> 12 (second gap 20).
    assert fit["usualQuantity"] == pytest.approx(11.6)
    assert fit["everyDays"] == pytest.approx(12.0)
    assert fit["dailyRate"] == pytest.approx(0.9 * 11.6 / 12.0)
    assert fit["typicalErrorDays"] is None  # one checked forecast is not enough


def test_croston_needs_three_purchases():
    d0 = date(2031, 1, 1)
    assert croston_sba([(d0, 5), (d0 + timedelta(days=7), 5)]) is None


def test_typical_error_is_the_mean_one_step_interval_error():
    d0 = date(2031, 1, 1)
    days = [0, 10, 20, 26, 40]
    fit = croston_sba([(d0 + timedelta(days=d), 4) for d in days], alpha=0.2)
    # Forecasts before each gap: 10 (gap 10, error 0), 10 (gap 6, error 4), 9.2 (gap 14, error 4.8).
    assert fit["typicalErrorDays"] == pytest.approx((0 + 4 + 4.8) / 3)


def test_office_records_and_removes_a_purchase(client, shop):
    today = shop_now().date().isoformat()
    body = {"toolId": shop["oil"].id, "purchasedOn": today, "quantity": 2, "unitCost": 850, "boughtFrom": "Hardware store"}
    assert client.post(URL, json=body, headers=_headers(shop["admin"])).status_code == 403
    assert client.post(URL, json=body, headers=_headers(shop["worker"])).status_code == 403
    res = client.post(URL, json=body, headers=_headers(shop["office"]))
    assert res.status_code == 201, res.get_json()
    row = res.get_json()
    assert row["unit"] == "gal" and row["amount"] == 1700.0 and row["source"] == "RECORDED"

    listed = client.get(URL, query_string={"toolId": shop["oil"].id}, headers=_headers(shop["admin"]))
    assert [r["id"] for r in listed.get_json()] == [row["id"]]

    assert client.delete(f"{URL}/{row['id']}", headers=_headers(shop["office"])).status_code == 200
    assert client.get(URL, headers=_headers(shop["office"])).get_json() == []
    assert db.session.get(ConsumablePurchase, row["id"]).voided_at is not None


def test_a_purchase_needs_a_quantity_and_a_past_date(client, shop):
    tomorrow = (shop_now().date() + timedelta(days=1)).isoformat()
    h = _headers(shop["office"])
    future = client.post(URL, json={"toolId": shop["oil"].id, "purchasedOn": tomorrow, "quantity": 1}, headers=h)
    assert future.status_code == 400
    none = client.post(URL, json={"toolId": shop["oil"].id, "quantity": 0}, headers=h)
    assert none.status_code == 400


def test_replenishment_statuses(app, shop):
    office, oil = shop["office"], shop["oil"]
    disc = Tool(code="CP-DISC", name="Cutting disc", category=ToolCategory.CONSUMABLE, unit="pcs")
    rod = Tool(code="CP-ROD", name="Welding rod", category=ToolCategory.CONSUMABLE, unit="pack")
    db.session.add_all([disc, rod])
    db.session.flush()
    today = date(2031, 6, 30)

    def buy(tool, days_ago, qty):
        db.session.add(
            ConsumablePurchase(
                tool_id=tool.id,
                purchased_on=today - timedelta(days=days_ago),
                quantity=Decimal(qty),
                unit=tool.unit,
                unit_cost=Decimal("10"),
                recorded_by_id=office.id,
            )
        )

    for d in (90, 60, 30):  # every 30 days, last 30 days ago: due today
        buy(oil, d, 1)
    for d in (40, 30, 20, 13):  # every ~9 days, last 13 days ago: overdue
        buy(disc, d, 50)
    buy(rod, 3, 5)  # one purchase: too few to forecast
    db.session.commit()

    data = replenishment_service.replenishment(today)
    by_name = {r["name"]: r for r in data["items"]}
    assert by_name["Cutting oil"]["status"] == "DUE_SOON"
    assert by_name["Cutting oil"]["nextPurchaseOn"] == today.isoformat()
    assert by_name["Cutting disc"]["status"] == "OVERDUE"
    assert by_name["Welding rod"]["status"] == "LEARNING"
    assert by_name["Welding rod"]["nextPurchaseOn"] is None
    assert [r["status"] for r in data["items"]][:2] == ["OVERDUE", "DUE_SOON"]
    assert data["summary"]["overdue"] == 1 and data["summary"]["dueSoon"] == 1
    # Within the last 30 days: two disc purchases and the rod (the oil was 30 days ago).
    assert data["summary"]["spentLast30Days"] == pytest.approx(50 * 10 * 2 + 5 * 10)


def test_a_purchase_can_set_the_unit_the_item_is_counted_in(client, shop):
    today = shop_now().date().isoformat()
    h = _headers(shop["office"])
    body = {"toolId": shop["oil"].id, "purchasedOn": today, "quantity": 3, "unit": "gallon"}
    res = client.post(URL, json=body, headers=h)
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["unit"] == "gallon"
    db.session.expire_all()
    assert db.session.get(Tool, shop["oil"].id).unit == "gallon"
    bad = client.post(URL, json={**body, "unit": "barrel"}, headers=h)
    assert bad.status_code == 400
