"""Consumable stock adjustments and per-unit tool borrow/return."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import qrcode

from app.extensions import db
from app.models.tool import Tool, ToolCategory
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.tool_type import ToolUnit, ToolUnitStatus
from app.utils.errors import AppError
from app.utils.row_locks import lock_row


def _dec(v, default="0") -> Decimal:
    if v is None or v == "":
        return Decimal(default)
    return Decimal(str(v))


def _parse_quantity(raw, *, default=Decimal("1")) -> Decimal:
    if raw is None or raw == "":
        return default
    try:
        qty = Decimal(str(raw))
    except Exception as exc:
        raise AppError("quantity must be a number", "VALIDATION_ERROR", 400) from exc
    if qty <= 0:
        raise AppError("quantity must be greater than zero", "VALIDATION_ERROR", 400)
    return qty


def _check_consumable_unit(unit: str):
    from app.models.material_catalog import CONSUMABLE_UNITS

    if unit not in CONSUMABLE_UNITS:
        raise AppError(
            f"Unit must be one of {', '.join(CONSUMABLE_UNITS)}", "VALIDATION_ERROR", 400
        )


def create_tool(data):
    """Create a consumable inventory item."""
    name = (data.get("name") or "").strip()
    if not name:
        raise AppError("Name is required", "VALIDATION_ERROR", 400)

    code = (data.get("code") or "").strip() or f"INV-{uuid.uuid4().hex[:8].upper()}"
    if Tool.query.filter_by(code=code).first():
        raise AppError("Item code already exists", "CONFLICT", 409)

    cat_raw = (data.get("category") or "CONSUMABLE").upper()
    if cat_raw != "CONSUMABLE":
        raise AppError(
            "Only CONSUMABLE items use this catalog; trackable tools use tool types/units",
            "VALIDATION_ERROR",
            400,
        )
    category = ToolCategory.CONSUMABLE

    raw_qty = data.get("quantityOnHand")
    if raw_qty not in (None, ""):
        try:
            qty = Decimal(str(raw_qty))
        except Exception as exc:
            raise AppError("quantityOnHand must be a number", "VALIDATION_ERROR", 400) from exc
        if qty < 0:
            raise AppError("quantityOnHand cannot be negative", "VALIDATION_ERROR", 400)
        if qty > 0:
            raise AppError(
                "New consumables start with no stock. Order them on a purchase order "
                "and receive it to add stock.",
                "VALIDATION_ERROR",
                400,
            )

    unit = (data.get("unit") or "pcs").strip() or "pcs"
    _check_consumable_unit(unit)

    min_stock = data.get("minimumStock")
    minimum = None if min_stock in (None, "") else _parse_quantity(min_stock, default=Decimal("0"))

    tool = Tool(
        name=name,
        code=code,
        category=category,
        unit=unit,
        quantity_on_hand=Decimal("0"),
        minimum_stock=minimum,
        size_spec=(data.get("sizeSpec") or None) or None,
        shop_term=(data.get("shopTerm") or "").strip() or None,
        active=bool(data["active"]) if "active" in data else True,
        ordered_from_supplier=bool(data.get("orderedFromSupplier")),
    )
    db.session.add(tool)
    db.session.commit()
    return tool


def update_tool(tool_id, data):
    tool = Tool.query.get(tool_id)
    if not tool:
        raise AppError("Item not found", "NOT_FOUND", 404)

    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise AppError("Name is required", "VALIDATION_ERROR", 400)
        tool.name = name

    if "code" in data:
        code = (data.get("code") or "").strip()
        if not code:
            raise AppError("Code is required", "VALIDATION_ERROR", 400)
        clash = Tool.query.filter(Tool.code == code, Tool.id != tool_id).first()
        if clash:
            raise AppError("Item code already exists", "CONFLICT", 409)
        tool.code = code

    if "unit" in data:
        unit = (data.get("unit") or "pcs").strip() or "pcs"
        # Legacy units stay as they are; only a change to a new unit is validated.
        if unit != tool.unit:
            _check_consumable_unit(unit)
        tool.unit = unit

    if "active" in data:
        tool.active = bool(data.get("active"))

    if "sizeSpec" in data:
        tool.size_spec = (data.get("sizeSpec") or None) or None

    if "orderedFromSupplier" in data:
        tool.ordered_from_supplier = bool(data.get("orderedFromSupplier"))

    if "shopTerm" in data:
        tool.shop_term = (data.get("shopTerm") or "").strip() or None

    if "minimumStock" in data:
        min_stock = data.get("minimumStock")
        tool.minimum_stock = (
            None
            if min_stock in (None, "")
            else _parse_quantity(min_stock, default=Decimal("0"))
        )

    db.session.commit()
    return tool


def generate_qr_png(payload: str):
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def scan_tool(code, worker_id, intent=None, quantity=None):
    """Borrow/return a specific ToolUnit by asset code QR.

    Prefer ``consumable_scan_service.unified_scan`` for the worker one-button flow.
    """
    unit = ToolUnit.query.filter_by(asset_code=code.strip()).first()
    if not unit:
        if Tool.query.filter_by(code=code.strip()).first():
            raise AppError(
                "Use the inventory scan endpoint for consumables",
                "VALIDATION_ERROR",
                400,
            )
        raise AppError("Tool unit not found", "NOT_FOUND", 404)
    # Two workers scanning the same unit: the second waits, then sees it is out.
    unit = lock_row(ToolUnit, unit.id)

    if unit.status == ToolUnitStatus.RETIRED:
        raise AppError("This tool is retired", "CONFLICT", 409)
    if unit.status == ToolUnitStatus.UNDER_REPAIR:
        raise AppError("This tool is under repair", "CONFLICT", 409)

    if intent:
        intent = intent.upper()
        if intent not in ("BORROW", "RETURN"):
            raise AppError("intent must be BORROW or RETURN", "VALIDATION_ERROR", 400)
        event_type = ToolEventType(intent)
    else:
        if unit.status == ToolUnitStatus.OUT and unit.current_holder_id == worker_id:
            event_type = ToolEventType.RETURN
        elif unit.status == ToolUnitStatus.AVAILABLE:
            event_type = ToolEventType.BORROW
        elif unit.status == ToolUnitStatus.OUT:
            raise AppError(
                f"Already out with {unit.current_holder.full_name if unit.current_holder else 'another worker'}",
                "CONFLICT",
                409,
            )
        else:
            event_type = ToolEventType.BORROW

    now = datetime.now(timezone.utc)

    if event_type == ToolEventType.BORROW:
        if unit.status != ToolUnitStatus.AVAILABLE:
            raise AppError(
                f"Unit is {unit.status.value}, not available to borrow",
                "CONFLICT",
                409,
            )
        unit.status = ToolUnitStatus.OUT
        unit.current_holder_id = worker_id
        unit.held_since = now
    else:
        if unit.status != ToolUnitStatus.OUT:
            raise AppError("This unit is not currently out", "CONFLICT", 409)
        if unit.current_holder_id != worker_id:
            raise AppError(
                "Only the current holder can return this tool",
                "CONFLICT",
                409,
            )
        unit.status = ToolUnitStatus.AVAILABLE
        unit.current_holder_id = None
        unit.held_since = None

    try:
        event = ToolEvent(
            tool_id=None,
            tool_unit_id=unit.id,
            worker_id=worker_id,
            type=event_type,
            quantity=Decimal("1"),
        )
        db.session.add(event)
        db.session.commit()
        return event
    except Exception:
        db.session.rollback()
        raise


def adjust_stock(tool_id, worker_id, quantity_delta, reason):
    """Admin/Office stock correction for consumables (not deliveries)."""
    tool = lock_row(Tool, tool_id)
    if not tool:
        raise AppError("Item not found", "NOT_FOUND", 404)
    if tool.category != ToolCategory.CONSUMABLE:
        raise AppError("Only consumables use stock adjust", "VALIDATION_ERROR", 400)
    reason = (reason or "").strip()
    if not reason:
        raise AppError("reason is required for adjustments", "VALIDATION_ERROR", 400)
    try:
        delta = Decimal(str(quantity_delta))
    except Exception as exc:
        raise AppError("quantity must be a number", "VALIDATION_ERROR", 400) from exc
    if delta == 0:
        raise AppError("quantity delta cannot be zero", "VALIDATION_ERROR", 400)

    new_qty = _dec(tool.quantity_on_hand) + delta
    if new_qty < 0:
        raise AppError(
            "Adjustment would make quantity on hand negative",
            "VALIDATION_ERROR",
            400,
        )
    tool.quantity_on_hand = new_qty
    event = ToolEvent(
        tool_id=tool.id,
        tool_unit_id=None,
        worker_id=worker_id,
        type=ToolEventType.ADJUST,
        quantity=abs(delta),
        stock_delta=delta,
        reason=reason,
    )
    db.session.add(event)
    db.session.commit()
    return event


def correct_stock(tool_id, worker_id, actual_count, reason):
    """Office stock correction: set on-hand to an actual count with a required reason."""
    tool = lock_row(Tool, tool_id)
    if not tool:
        raise AppError("Item not found", "NOT_FOUND", 404)
    if tool.category != ToolCategory.CONSUMABLE:
        raise AppError("Only consumables use stock correction", "VALIDATION_ERROR", 400)
    reason_s = (reason or "").strip()
    if not reason_s:
        raise AppError("reason is required for stock correction", "VALIDATION_ERROR", 400)
    try:
        counted = Decimal(str(actual_count))
    except Exception as exc:
        raise AppError("actualCount must be a number", "VALIDATION_ERROR", 400) from exc
    if counted < 0:
        raise AppError("actualCount cannot be negative", "VALIDATION_ERROR", 400)

    before = _dec(tool.quantity_on_hand)
    delta = counted - before
    if delta == 0:
        raise AppError("Count matches on hand; nothing to correct", "VALIDATION_ERROR", 400)

    tool.quantity_on_hand = counted
    event = ToolEvent(
        tool_id=tool.id,
        tool_unit_id=None,
        worker_id=worker_id,
        type=ToolEventType.CORRECTION,
        quantity=abs(delta),
        stock_delta=delta,
        reason=reason_s,
    )
    db.session.add(event)
    db.session.commit()
    return event


def receive_stock(tool_id, worker_id, quantity, supplier, received_on=None, note=None):
    """Record a consumable delivery (RECEIVE). Increases quantity on hand."""
    from datetime import date as date_cls

    from app.services.schedule_calendar import shop_now

    tool = lock_row(Tool, tool_id)
    if not tool:
        raise AppError("Item not found", "NOT_FOUND", 404)
    if tool.category != ToolCategory.CONSUMABLE:
        raise AppError("Only consumables use receive delivery", "VALIDATION_ERROR", 400)

    qty = _parse_quantity(quantity)
    supplier_s = (supplier or "").strip()
    if not supplier_s:
        raise AppError("supplier is required", "VALIDATION_ERROR", 400)

    if received_on:
        try:
            on_date = date_cls.fromisoformat(str(received_on)[:10])
        except ValueError as exc:
            raise AppError("receivedOn must be YYYY-MM-DD", "VALIDATION_ERROR", 400) from exc
    else:
        on_date = shop_now().date()

    note_s = (note or "").strip() or None
    tool.quantity_on_hand = _dec(tool.quantity_on_hand) + qty
    event = ToolEvent(
        tool_id=tool.id,
        tool_unit_id=None,
        worker_id=worker_id,
        type=ToolEventType.RECEIVE,
        quantity=qty,
        stock_delta=qty,
        reason=note_s,
        supplier=supplier_s,
        received_on=on_date,
    )
    db.session.add(event)
    db.session.commit()
    return event


MOVEMENT_TYPES = (
    ToolEventType.RECEIVE,
    ToolEventType.SCAN,
    ToolEventType.UNDO,
    ToolEventType.CORRECTION,
    ToolEventType.ADJUST,
)


def list_stock_movements(tool_id=None, page=1, per_page=50):
    """Consumable movement log for Office Staff."""
    q = ToolEvent.query.filter(
        ToolEvent.tool_id.isnot(None),
        ToolEvent.type.in_(MOVEMENT_TYPES),
    )
    if tool_id:
        q = q.filter_by(tool_id=tool_id)
    q = q.order_by(ToolEvent.created_at.desc())
    return q.paginate(page=page, per_page=min(per_page, 100), error_out=False)


def list_held_tools(worker_id):
    """Units currently held by this worker."""
    units = (
        ToolUnit.query.filter_by(
            current_holder_id=worker_id, status=ToolUnitStatus.OUT
        )
        .order_by(ToolUnit.asset_code)
        .all()
    )
    return [u.to_dict() for u in units]


def list_tool_events(tool_id=None, page=1, per_page=50, category=None):
    query = ToolEvent.query.order_by(ToolEvent.created_at.desc())
    if tool_id:
        query = query.filter_by(tool_id=tool_id)
    if category:
        cat = category.upper()
        if cat in ("CONSUMABLE",):
            query = query.join(Tool, ToolEvent.tool_id == Tool.id).filter(
                Tool.category == ToolCategory.CONSUMABLE
            )
        elif cat in ("TOOL", "TOOL_UNIT", "RETURNABLE_TOOL"):
            query = query.filter(ToolEvent.tool_unit_id.isnot(None))
        else:
            raise AppError(
                "category must be CONSUMABLE or TOOL",
                "VALIDATION_ERROR",
                400,
            )
    return query.paginate(page=page, per_page=per_page, error_out=False)


def list_worker_tool_events(worker_id, page=1, per_page=50):
    return (
        ToolEvent.query.filter_by(worker_id=worker_id)
        .filter(ToolEvent.tool_unit_id.isnot(None))
        .order_by(ToolEvent.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
