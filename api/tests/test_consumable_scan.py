"""Old consumable scan helpers (kept for history) and the refusal of new consumable scans."""

from decimal import Decimal

import pytest

from app.extensions import bcrypt, db
from app.models.tool import Tool, ToolCategory
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.user import User, UserRole, UserStatus
from app.services import consumable_scan_service as scan_svc
from app.services import tool_event_service as te_svc


@pytest.fixture
def worker(app):
    u = User(
        email="scan_worker@test.local",
        mobile_number="+639171110001",
        password_hash=bcrypt.generate_password_hash("Worker123!").decode("utf-8"),
        full_name="Scan Worker",
        role=UserRole.PRODUCTION_WORKER,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def oil(app):
    t = Tool(
        name="Cutting oil",
        code="TEST-OIL-BOTTLE",
        category=ToolCategory.CONSUMABLE,
        unit="bottle",
        quantity_on_hand=Decimal("5"),
    )
    db.session.add(t)
    db.session.commit()
    return t


def test_scan_deducts_one_and_sets_stock_delta(app, worker, oil):
    result = scan_svc.scan_consumable(oil, worker.id)
    assert result["needsConfirm"] is False
    assert "−1 bottle" in (result.get("displayLine") or "")
    db.session.refresh(oil)
    assert oil.quantity_on_hand == Decimal("4")
    ev = ToolEvent.query.filter_by(type=ToolEventType.SCAN).one()
    assert ev.stock_delta == Decimal("-1")
    assert ev.undone_at is None


def test_debounce_asks_confirm_then_second_scan(app, worker, oil):
    first = scan_svc.scan_consumable(oil, worker.id)
    again = scan_svc.scan_consumable(oil, worker.id)
    assert again["needsConfirm"] is True
    assert ToolEvent.query.filter_by(type=ToolEventType.SCAN).count() == 1
    confirmed = scan_svc.scan_consumable(oil, worker.id, confirm_repeat=True)
    assert confirmed["needsConfirm"] is False
    assert ToolEvent.query.filter_by(type=ToolEventType.SCAN).count() == 2
    db.session.refresh(oil)
    assert oil.quantity_on_hand == Decimal("3")
    assert first["id"] != confirmed["id"]


def test_client_action_id_is_idempotent(app, worker, oil):
    a = scan_svc.scan_consumable(oil, worker.id, client_action_id="scan-act-1")
    b = scan_svc.scan_consumable(oil, worker.id, client_action_id="scan-act-1")
    assert a["id"] == b["id"]
    assert ToolEvent.query.filter_by(type=ToolEventType.SCAN).count() == 1


def test_undo_restores_stock(app, worker, oil):
    result = scan_svc.scan_consumable(oil, worker.id)
    undo = scan_svc.undo_consumable_scan(result["id"], worker.id)
    assert undo["kind"] == "CONSUMABLE_UNDO"
    db.session.refresh(oil)
    assert oil.quantity_on_hand == Decimal("5")
    scan = ToolEvent.query.get(result["id"])
    assert scan.undone_at is not None


def test_receive_and_correct_set_stock_delta(app, worker, oil):
    te_svc.receive_stock(oil.id, worker.id, 3, "Demo Supplier")
    db.session.refresh(oil)
    assert oil.quantity_on_hand == Decimal("8")
    recv = ToolEvent.query.filter_by(type=ToolEventType.RECEIVE).order_by(ToolEvent.created_at.desc()).first()
    assert recv.stock_delta == Decimal("3")

    te_svc.correct_stock(oil.id, worker.id, actual_count=6, reason="Missed scans")
    db.session.refresh(oil)
    assert oil.quantity_on_hand == Decimal("6")
    corr = ToolEvent.query.filter_by(type=ToolEventType.CORRECTION).one()
    assert corr.stock_delta == Decimal("-2")


def test_unified_scan_refuses_consumable_codes(app, worker, oil):
    """Consumables are no longer scanned; the shop records what it buys instead."""
    from app.utils.errors import AppError

    with pytest.raises(AppError) as err:
        scan_svc.unified_scan(oil.code, worker.id)
    assert err.value.code == "CONSUMABLE_NOT_SCANNED"
    db.session.refresh(oil)
    assert oil.quantity_on_hand == Decimal("5")


def test_tool_scan_says_borrowed_or_returned_in_plain_words(app, worker):
    from app.models.tool_type import ToolType, ToolUnit

    caliper = ToolType(name="Vernier caliper", code="VCX")
    db.session.add(caliper)
    db.session.flush()
    db.session.add(ToolUnit(tool_type_id=caliper.id, asset_code="VCX-01"))
    db.session.commit()

    first = scan_svc.unified_scan("VCX-01", worker.id)
    assert first["displayLine"] == "Borrowed: Vernier caliper (VCX-01)"
    again = scan_svc.unified_scan("VCX-01", worker.id)
    assert again["displayLine"] == "Returned: Vernier caliper (VCX-01)"
