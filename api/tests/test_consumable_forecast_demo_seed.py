"""FC-DEMO consumable forecast seed helper."""

from decimal import Decimal

from app.extensions import db
from app.models.supplier import Supplier
from app.models.tool import Tool, ToolCategory
from app.models.user import User, UserRole, UserStatus
from app.seed.consumable_forecast_demo import (
    NO_DATA_TOOL_CODE,
    seed_consumable_forecast_demo,
    wipe_consumable_forecast_demo,
)
from app.services import forecast_service as fs
from app.services.schedule_calendar import shop_now


def _worker(email="worker1@bmsc.local"):
    w = User(
        email=email,
        password_hash="x",
        full_name="Demo Worker",
        role=UserRole.PRODUCTION_WORKER,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(w)
    db.session.flush()
    return w


def test_forecast_demo_seed_mixes_labels(app):
    db.session.add_all(
        [
            Supplier(name="Railim Industrial Corporation", code="RIC", typical_lead_time_days=7),
            Supplier(name="STP Industrial Inc.", code="STP", typical_lead_time_days=5),
        ]
    )
    _worker()
    tools = {
        code: Tool(
            name=code,
            code=code,
            category=ToolCategory.CONSUMABLE,
            unit="pcs",
            quantity_on_hand=Decimal("10"),
            minimum_stock=Decimal("3"),
        )
        for code in (
            NO_DATA_TOOL_CODE,
            "INV-HANDTAP-M10",
            "INV-HANDTAP-M8",
            "INV-DRILL-06",
            "INV-FLAP-115",
            "INV-ENDMILL-10",
            "INV-OIL-CUT",
            "INV-CUT-115",
        )
    }
    db.session.add_all(tools.values())
    db.session.commit()

    wipe_consumable_forecast_demo()
    stats = seed_consumable_forecast_demo(replace=True)
    assert stats["skipped"] is False
    db.session.commit()

    today = shop_now().date()
    rows = {r["code"]: r for r in fs.consumable_run_out(today=today)["items"]}
    assert rows[NO_DATA_TOOL_CODE]["label"] == "No data yet"
    assert rows["INV-HANDTAP-M10"]["label"] == "Estimated"
    assert rows["INV-HANDTAP-M8"]["label"] == "Estimated"
    assert rows["INV-DRILL-06"]["label"] == "Computed"
    assert rows["INV-DRILL-06"]["status"] in ("REORDER_NOW", "REORDER_SOON", "OK")
