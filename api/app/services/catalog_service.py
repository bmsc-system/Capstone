"""Unified catalog: job raw materials and consumables in one list.

Materials live in ``material_catalog``; consumables are ``tools`` rows
(category CONSUMABLE), including ones not stocked yet (no stock, events or PO
lines). Stock only arrives by receiving a purchase order.
"""

from __future__ import annotations

from app.extensions import db
from app.models.material_catalog import (
    CONSUMABLE_UNITS,
    MATERIAL_CATEGORIES,
    MATERIAL_UNITS,
    MaterialCatalogItem,
)
from app.models.tool import Tool, ToolCategory
from app.services import material_catalog_service as materials
from app.services import tool_event_service as tools
from app.utils.errors import AppError

KINDS = ("MATERIAL", "CONSUMABLE")
CONSUMABLE_CATEGORY = "Consumable"


def _num(v):
    return float(v) if v is not None else None


def material_item(item: MaterialCatalogItem) -> dict:
    return {
        "id": item.id,
        "kind": "MATERIAL",
        "name": item.name,
        "shopTerm": item.shop_term,
        "code": None,
        "gradesOrSizes": list(item.grades or []),
        "grades": list(item.grades or []),
        "unit": item.default_unit,
        "defaultUnit": item.default_unit,
        "category": item.category,
        "active": bool(item.active),
        "notStockedYet": None,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }


def consumable_item(tool: Tool, *, stocked: bool | None = None) -> dict:
    stocked = tool.is_stocked if stocked is None else stocked
    return {
        "id": tool.id,
        "kind": "CONSUMABLE",
        "name": tool.name,
        "shopTerm": tool.shop_term,
        "code": tool.code,
        "gradesOrSizes": [tool.size_spec] if tool.size_spec else [],
        "sizeSpec": tool.size_spec,
        "unit": tool.unit,
        "category": CONSUMABLE_CATEGORY,
        "active": bool(tool.active) if tool.active is not None else True,
        "notStockedYet": not stocked,
        "orderedFromSupplier": bool(tool.ordered_from_supplier),
        "quantityOnHand": _num(tool.quantity_on_hand),
        "minimumStock": _num(tool.minimum_stock),
        "createdAt": tool.created_at.isoformat() if tool.created_at else None,
    }


def _consumables(search, include_inactive):
    q = Tool.query.filter(Tool.category == ToolCategory.CONSUMABLE)
    if not include_inactive:
        q = q.filter(Tool.active.is_(True))
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(
            Tool.name.ilike(like)
            | Tool.shop_term.ilike(like)
            | Tool.code.ilike(like)
            | Tool.size_spec.ilike(like)
        )
    return q.order_by(db.func.lower(Tool.name)).all()


def list_unified(*, search=None, type_filter="all", include_inactive=False) -> list[dict]:
    """Materials and consumables as one list sorted by name. ``type_filter`` is
    all | MATERIAL | CONSUMABLE (case-insensitive)."""
    kind = (type_filter or "all").strip().upper()
    if kind not in ("ALL", *KINDS):
        raise AppError("type must be all, MATERIAL or CONSUMABLE", "VALIDATION_ERROR", 400)
    search = (search or "").strip() or None

    items = []
    if kind in ("ALL", "MATERIAL"):
        items += [
            material_item(m)
            for m in materials.list_items(search=search, include_inactive=include_inactive)
        ]
    if kind in ("ALL", "CONSUMABLE"):
        items += [consumable_item(t) for t in _consumables(search, include_inactive)]
    items.sort(key=lambda i: (i["name"] or "").lower())
    return items


def reference_lists() -> dict:
    return {
        "materialUnits": MATERIAL_UNITS,
        "consumableUnits": CONSUMABLE_UNITS,
        "categories": MATERIAL_CATEGORIES,
    }


def _kind(value) -> str:
    kind = str(value or "").strip().upper()
    if kind not in KINDS:
        raise AppError("kind must be MATERIAL or CONSUMABLE", "VALIDATION_ERROR", 400)
    return kind


def _material_payload(data: dict) -> dict:
    body = dict(data)
    if "defaultUnit" not in body and "unit" in body:
        body["defaultUnit"] = body["unit"]
    if "grades" not in body and "gradesOrSizes" in body:
        body["grades"] = body["gradesOrSizes"]
    return body


def create_item(data: dict) -> dict:
    """Add a material or a consumable (always with zero stock)."""
    kind = _kind(data.get("kind"))
    if kind == "MATERIAL":
        return material_item(materials.create_item(_material_payload(data)))
    body = {**data, "category": "CONSUMABLE"}
    return consumable_item(tools.create_tool(body), stocked=False)


def update_item(kind, item_id, data: dict) -> dict:
    kind = _kind(kind)
    if kind == "MATERIAL":
        return material_item(materials.update_item(item_id, _material_payload(data)))
    tool = Tool.query.get(item_id)
    if not tool or tool.category != ToolCategory.CONSUMABLE:
        raise AppError("Consumable not found", "NOT_FOUND", 404)
    if "quantityOnHand" in data:
        raise AppError(
            "Stock changes only by receiving a purchase order or a stock correction.",
            "VALIDATION_ERROR",
            400,
        )
    return consumable_item(tools.update_tool(item_id, data))
