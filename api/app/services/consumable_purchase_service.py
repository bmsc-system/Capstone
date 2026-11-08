"""Consumable purchases: recorded by the Office Staff, or received on a supplier order.

Both feed the replenishment forecast. A recorded purchase entered by mistake
is voided, not deleted, so the audit log still shows it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models.consumable_purchase import ConsumablePurchase
from app.models.material_purchase import MaterialPurchase
from app.models.tool import Tool, ToolCategory
from app.services.schedule_calendar import shop_now
from app.utils.errors import AppError

MAX_TEXT = {"boughtFrom": 120, "note": 255}


def _bad(message):
    return AppError(message, "VALIDATION_ERROR", 400)


def _decimal(value, label, *, allow_zero=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise _bad(f"Enter the {label}.")
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise _bad(f"Enter the {label}.")
    return number


def _text(data, key):
    value = (data.get(key) or "").strip() or None
    if value and len(value) > MAX_TEXT[key]:
        raise _bad(f"Keep it under {MAX_TEXT[key]} characters.")
    return value


def _consumable(tool_id) -> Tool:
    tool = db.session.get(Tool, tool_id) if tool_id else None
    if tool is None or tool.category != ToolCategory.CONSUMABLE:
        raise _bad("Pick a consumable.")
    return tool


def record_purchase(data: dict, user_id: str) -> ConsumablePurchase:
    tool = _consumable(data.get("toolId"))
    if not tool.active:
        raise _bad(f"{tool.name} is no longer offered in the catalog.")
    raw_day = data.get("purchasedOn")
    try:
        day = date.fromisoformat(str(raw_day)) if raw_day else shop_now().date()
    except ValueError:
        raise _bad("Enter the date it was bought.")
    if day > shop_now().date():
        raise _bad("The purchase date can't be in the future.")
    quantity = _decimal(data.get("quantity"), "quantity bought")
    cost = data.get("unitCost")
    unit_cost = None if cost in (None, "") else _decimal(cost, "unit cost", allow_zero=True)
    purchase = ConsumablePurchase(
        tool_id=tool.id,
        purchased_on=day,
        quantity=quantity,
        unit=tool.unit or "pcs",
        unit_cost=unit_cost,
        bought_from=_text(data, "boughtFrom"),
        note=_text(data, "note"),
        recorded_by_id=user_id,
    )
    db.session.add(purchase)
    db.session.flush()
    # Buying the item answers any running-low report from the shop floor.
    from app.services.consumable_request_service import close_on_purchase

    close_on_purchase(tool.id, user_id, consumable_purchase_id=purchase.id)
    db.session.commit()
    return purchase


def void_purchase(purchase_id: str, user_id: str) -> ConsumablePurchase:
    purchase = db.session.get(ConsumablePurchase, purchase_id)
    if purchase is None:
        raise AppError("Purchase not found", "NOT_FOUND", 404)
    if purchase.voided_at is not None:
        raise _bad("This purchase was already removed.")
    purchase.voided_at = datetime.now(timezone.utc)
    purchase.voided_by_id = user_id
    db.session.commit()
    return purchase


def _po_line(line: MaterialPurchase) -> dict:
    qty = float(line.quantity)
    cost = float(line.unit_cost) if line.unit_cost is not None else None
    return {
        "id": line.id,
        "source": "SUPPLIER_ORDER",
        "toolId": line.tool_id,
        "name": line.material_name,
        "purchasedOn": line.date_received.isoformat(),
        "quantity": qty,
        "unit": line.unit,
        "unitCost": cost,
        "amount": round(qty * cost, 2) if cost is not None else None,
        "boughtFrom": line.supplier.name if line.supplier else None,
        "note": None,
        "poNumber": line.supplier_order.po_number if line.supplier_order else None,
        "recordedBy": None,
        "createdAt": line.created_at.isoformat() if line.created_at else None,
    }


def purchase_rows(tool_ids=None, since: date | None = None) -> list[dict]:
    """Every purchase of a consumable, newest first: recorded ones and received PO lines."""
    recorded = ConsumablePurchase.query.filter(ConsumablePurchase.voided_at.is_(None))
    received = MaterialPurchase.query.filter(
        MaterialPurchase.tool_id.isnot(None),
        MaterialPurchase.cancelled_at.is_(None),
        MaterialPurchase.date_received.isnot(None),
    )
    if tool_ids is not None:
        ids = list(tool_ids)
        if not ids:
            return []
        recorded = recorded.filter(ConsumablePurchase.tool_id.in_(ids))
        received = received.filter(MaterialPurchase.tool_id.in_(ids))
    if since is not None:
        recorded = recorded.filter(ConsumablePurchase.purchased_on >= since)
        received = received.filter(MaterialPurchase.date_received >= since)
    rows = [p.to_dict() for p in recorded.all()] + [_po_line(ln) for ln in received.all()]
    rows.sort(key=lambda r: (r["purchasedOn"], r["createdAt"] or ""), reverse=True)
    return rows
