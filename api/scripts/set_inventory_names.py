"""Put the shop's own consumables, tools and raw materials in place.

The lists are in app/constants/inventory_names.py. For each kind:

* A record already under the shop's name, or under an older spelling of it
  (an alias), is kept and renamed, so its stock and history stay with it.
  Several sizes of the same consumable all take the name.
* A name on the list with no record yet is created (stock 0, no tool units:
  the Office adds units with their QR codes).
* Anything else is a sample or test entry. It is deleted when nothing points
  at it. When it has history (hand-outs, purchases, PO lines, borrowing) it
  is archived instead: hidden from lists and pickers, kept on past records.
  A tool with borrowing history keeps its type and has its units retired.

Preview (changes nothing):
    .venv\\Scripts\\python.exe scripts\\set_inventory_names.py

Save, refusing unless the number of changes equals --expect:
    .venv\\Scripts\\python.exe scripts\\set_inventory_names.py --apply --expect N
"""

from __future__ import annotations

import argparse
from decimal import Decimal

from _target_db import confirm_database, open_app


def _exact_first(rows, specs, match):
    """Rows already under a listed name first, so they keep it and an alias
    match (only one row may hold a name) does not take it from them."""
    names = {s["name"].lower() for s in specs}

    def key(row):
        spec = match(specs, row.name)
        return (0 if spec and row.name.lower() in names else 1, not getattr(row, "active", True), row.name)

    return sorted(rows, key=key)


def _plan(db, models, lists):
    """[(kind, action, text, apply_fn)] in the order they run."""
    Tool, ToolCategory, ToolType, ToolUnit, ToolUnitStatus, ToolEvent, ToolEventType, Catalog, MaterialPurchase, refs = models
    CONSUMABLES, TOOLS, MATERIALS, match = lists
    steps = []

    # ---- consumables -------------------------------------------------------
    taken_codes = {c for (c,) in db.session.query(Tool.code).all()}
    found = set()
    for tool in Tool.query.order_by(Tool.name, Tool.code).all():
        spec = match(CONSUMABLES, tool.name)
        label = f"{tool.name} ({tool.code}{', ' + tool.size_spec if tool.size_spec else ''})"
        if spec:
            found.add(spec["name"])
            changes = []
            if tool.name != spec["name"]:
                changes.append(("name", spec["name"]))
                if spec.get("old_name_as_spec") and not tool.size_spec:
                    changes.append(("size_spec", tool.name))
            if not tool.active:
                changes.append(("active", True))
            if spec.get("shop_term") and not tool.shop_term:
                changes.append(("shop_term", spec["shop_term"]))
            if spec.get("ordered_from_supplier") and not tool.ordered_from_supplier:
                changes.append(("ordered_from_supplier", True))
            if changes:
                text = ", ".join(f"{k} → {v}" for k, v in changes)
                steps.append(("Consumable", "keep", f"{label}: {text}",
                              lambda t=tool, c=changes: [setattr(t, k, v) for k, v in c]))
            continue
        used = refs["tool"](tool.id)
        if used:
            if tool.active:
                steps.append(("Consumable", "archive", f"{label}: has history ({used}), hidden",
                              lambda t=tool: setattr(t, "active", False)))
        else:
            steps.append(("Consumable", "delete", f"{label}: never used",
                          lambda t=tool: db.session.delete(t)))
    for spec in CONSUMABLES:
        if spec["name"] in found:
            continue
        code, n = spec["code"], 2
        while code in taken_codes:
            code, n = f"{spec['code']}-{n}", n + 1
        taken_codes.add(code)
        steps.append(("Consumable", "create", f"{spec['name']} ({code}, {spec['unit']})",
                      lambda s=spec, c=code: db.session.add(Tool(
                          name=s["name"], code=c, category=ToolCategory.CONSUMABLE, unit=s["unit"],
                          quantity_on_hand=Decimal("0"), minimum_stock=None, shop_term=s.get("shop_term"),
                          ordered_from_supplier=bool(s.get("ordered_from_supplier")), active=True))))

    # ---- tools -------------------------------------------------------------
    taken_codes = {c for (c,) in db.session.query(ToolType.code).all()}
    found = set()
    for tt in _exact_first(ToolType.query.order_by(ToolType.name).all(), TOOLS, match):
        spec = match(TOOLS, tt.name)
        units = list(tt.units or [])
        label = f"{tt.name} ({len(units)} unit{'s' if len(units) != 1 else ''})"
        if spec and spec["name"] not in found:
            found.add(spec["name"])
            if tt.name != spec["name"]:
                steps.append(("Tool", "keep", f"{label}: name → {spec['name']}",
                              lambda t=tt, s=spec: setattr(t, "name", s["name"])))
            continue
        unit_ids = [u.id for u in units]
        borrowed = unit_ids and db.session.query(ToolEvent.id).filter(
            ToolEvent.tool_unit_id.in_(unit_ids),
            ToolEvent.type.in_([ToolEventType.BORROW, ToolEventType.RETURN]),
        ).first()
        if any(u.status == ToolUnitStatus.OUT for u in units):
            steps.append(("Tool", "skip", f"{label}: a unit is borrowed out; return it first, then run again", None))
        elif borrowed:
            live = [u for u in units if u.status != ToolUnitStatus.RETIRED]
            if live:
                steps.append(("Tool", "retire", f"{label}: has borrowing history, units retired",
                              lambda us=live: [setattr(u, "status", ToolUnitStatus.RETIRED) for u in us]))
        else:
            def _drop(t=tt, us=units):
                ids = [u.id for u in us]
                if ids:
                    ToolEvent.query.filter(ToolEvent.tool_unit_id.in_(ids)).delete(synchronize_session=False)
                for u in us:
                    db.session.delete(u)
                db.session.delete(t)
            steps.append(("Tool", "delete", f"{label}: never borrowed", _drop))
    for spec in TOOLS:
        if spec["name"] in found:
            continue
        code, n = spec["code"], 2
        while code in taken_codes:
            code, n = f"{spec['code']}-{n}", n + 1
        taken_codes.add(code)
        steps.append(("Tool", "create", f"{spec['name']} ({code}, no units yet)",
                      lambda s=spec, c=code: db.session.add(ToolType(name=s["name"], code=c, is_seed=False))))

    # ---- raw materials -----------------------------------------------------
    found = set()
    for item in _exact_first(Catalog.query.order_by(Catalog.name).all(), MATERIALS, match):
        spec = match(MATERIALS, item.name)
        label = item.name
        if spec and spec["name"] not in found:
            found.add(spec["name"])
            changes = []
            if item.name != spec["name"]:
                changes.append(("name", spec["name"]))
            if not item.active:
                changes.append(("active", True))
            if spec.get("shop_term") and not item.shop_term:
                changes.append(("shop_term", spec["shop_term"]))
            if spec.get("category") and not item.category:
                changes.append(("category", spec["category"]))
            if changes:
                text = ", ".join(f"{k} → {v}" for k, v in changes)
                steps.append(("Material", "keep", f"{label}: {text}",
                              lambda i=item, c=changes: [setattr(i, k, v) for k, v in c]))
            continue
        used = db.session.query(MaterialPurchase.id).filter(MaterialPurchase.material_catalog_id == item.id).count()
        if used:
            if item.active:
                steps.append(("Material", "archive", f"{label}: on {used} purchase line(s), hidden",
                              lambda i=item: setattr(i, "active", False)))
        else:
            steps.append(("Material", "delete", f"{label}: never used", lambda i=item: db.session.delete(i)))
    for spec in MATERIALS:
        if spec["name"] in found:
            continue
        steps.append(("Material", "create", f"{spec['name']} ({spec['category']}, {spec['default_unit']})",
                      lambda s=spec: db.session.add(Catalog(
                          name=s["name"], shop_term=s.get("shop_term"), grades=[], default_unit=s["default_unit"],
                          category=s["category"], active=True))))
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Save the changes")
    parser.add_argument("--expect", type=int, help="Number of changes the preview showed")
    args = parser.parse_args()

    app, label = open_app()

    from app.constants.inventory_names import CONSUMABLES, MATERIALS, TOOLS, match
    from app.extensions import db
    from app.models.consumable_purchase import ConsumablePurchase
    from app.models.consumable_request import ConsumableRequest
    from app.models.material_catalog import MaterialCatalogItem
    from app.models.material_purchase import MaterialPurchase
    from app.models.stocktake import StocktakeLine
    from app.models.tool import Tool, ToolCategory
    from app.models.tool_event import ToolEvent, ToolEventType
    from app.models.tool_type import ToolType, ToolUnit, ToolUnitStatus

    def tool_refs(tool_id):
        parts = []
        for model, what in (
            (ToolEvent, "stock events"),
            (ConsumablePurchase, "purchases"),
            (ConsumableRequest, "requests"),
            (StocktakeLine, "counts"),
            (MaterialPurchase, "PO lines"),
        ):
            n = db.session.query(model.id).filter(model.tool_id == tool_id).count()
            if n:
                parts.append(f"{n} {what}")
        return ", ".join(parts)

    models = (Tool, ToolCategory, ToolType, ToolUnit, ToolUnitStatus, ToolEvent, ToolEventType,
              MaterialCatalogItem, MaterialPurchase, {"tool": tool_refs})

    with app.app_context():
        print(f"Target database: {label}")
        steps = _plan(db, models, (CONSUMABLES, TOOLS, MATERIALS, match))
        changes = [s for s in steps if s[3] is not None]
        for kind in ("Consumable", "Tool", "Material"):
            mine = [s for s in steps if s[0] == kind]
            print(f"\n{kind}s:")
            if not mine:
                print("  (already as listed)")
            for _, action, text, _fn in mine:
                print(f"  {action:<8} {text}")
        print(f"\n{len(changes)} change(s).")
        if not args.apply:
            print("Preview: nothing changed. Run again with --apply --expect N to save.")
            return
        if args.expect is None or args.expect != len(changes):
            raise SystemExit(f"Found {len(changes)}, --expect was {args.expect}. Nothing changed.")
        confirm_database(label)
        try:
            for _, _, _, fn in changes:
                fn()
                db.session.flush()
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise SystemExit(f"Nothing changed: {exc}")
        print(f"Saved {len(changes)} change(s).")


if __name__ == "__main__":
    main()
