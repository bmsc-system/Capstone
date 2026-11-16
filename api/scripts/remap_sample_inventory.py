"""Put the sample history on the shop's own materials and consumables.

The sample job orders, PO lines and hand-outs were made with an older list
(Cast iron, SKD 11, Brass, Aluminium 5052, welding rod, safety glasses...).
The shop's list is in app/constants/inventory_names.py. This moves the
sample history onto that list so analytics, orders and job orders show
only what the shop buys:

* Raw materials not on the list take the nearest one on it: copper and
  brass are Bronze Materials, tool steels are 4140, steel and aluminium
  plate or sheet is MS Plates or Stainless Plates, and any round bar
  ("dia" in its size) of those is Shafting. MS Plates and Stainless Plates
  lines that are round bars become Shafting too. This covers PO lines and
  the planned materials on job orders. Catalog entries left with nothing
  are deleted.
* A center drill is a drill bit, so it joins Drill Bits ("Center drill A" as its size).
* Other consumables not on the list (welding rod, MIG wire, gloves, safety
  glasses, rags, inserts, lathe bits) have only sample history. Their
  hand-outs, purchases and counts are deleted with them. One with any record
  that is not sample data is left alone and listed.

Preview (changes nothing):
    .venv\\Scripts\\python.exe scripts\\remap_sample_inventory.py

Save, refusing unless the number of changes equals --expect:
    .venv\\Scripts\\python.exe scripts\\remap_sample_inventory.py --apply --expect N
"""

from __future__ import annotations

import argparse
from collections import Counter

from _target_db import confirm_database, open_app

# Old raw material -> the shop's material for plate/sheet or other shapes.
# Round bar of a steel or aluminium goes to Shafting instead (see _target).
MATERIAL_MAP = {
    "copper": "Bronze Materials",
    "brass": "Bronze Materials",
    "o1 tool steel": "4140",
    "skd 11": "4140",
    "skd 61 tool steel": "4140",
    "cast iron": "MS Plates",
    "a36": "MS Plates",
    "carbon steel": "MS Plates",
    "galvanized iron sheet": "MS Plates",
    "aluminium 1100": "Stainless Plates",
    "aluminium 7075": "Stainless Plates",
    "5052": "Stainless Plates",
    "6061": "Stainless Plates",
}
PLATES = {"MS Plates", "Stainless Plates"}

# Old consumable -> listed consumable it really is (its old name stays as the size).
CONSUMABLE_MAP = {"center drill": "Drill Bits"}


def _is_round(size: str | None) -> bool:
    return "dia" in (size or "").lower()


def _target(name: str | None, size: str | None, on_list) -> str | None:
    """The shop's material for this line, or None to leave it."""
    key = " ".join((name or "").lower().split())
    target = MATERIAL_MAP.get(key)
    if target is None:
        spec = on_list(name)
        if spec is None:
            return None
        target = spec["name"]
    if target in PLATES and _is_round(size):
        target = "Shafting"
    return target if target != name else None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Save the changes")
    parser.add_argument("--expect", type=int, help="Number of changes the preview showed")
    args = parser.parse_args()

    app, label = open_app()

    from sqlalchemy.orm.attributes import flag_modified

    from app.constants.inventory_names import CONSUMABLES, MATERIALS, match
    from app.extensions import db
    from app.models.consumable_purchase import ConsumablePurchase
    from app.models.consumable_request import ConsumableRequest
    from app.models.job_order import JobOrder
    from app.models.material_catalog import MaterialCatalogItem as Catalog
    from app.models.material_purchase import MaterialPurchase
    from app.models.sample_record import SampleRecord
    from app.models.stocktake import StocktakeLine
    from app.models.tool import Tool, ToolCategory
    from app.models.tool_event import ToolEvent

    def on_list(name):
        return match(MATERIALS, name)

    with app.app_context():
        print(f"Target database: {label}")
        catalog = {c.name: c for c in Catalog.query.all()}
        missing = sorted((set(MATERIAL_MAP.values()) | {"Shafting"}) - set(catalog))
        if missing:
            raise SystemExit(f"Not in the catalog yet: {', '.join(missing)}. Run set_inventory_names.py first.")

        steps = []  # (kind, text, fn)

        # ---- PO lines ----------------------------------------------------------
        moves = Counter()
        line_fixes = []
        for ln in MaterialPurchase.query.filter(MaterialPurchase.tool_id.is_(None)).all():
            target = _target(ln.material_name, ln.size_spec, on_list)
            if target:
                moves[(ln.material_name, target)] += 1
                line_fixes.append((ln, target))
        for (old, new), n in sorted(moves.items()):
            steps.append(("PO lines", f"{old} → {new} ({n})", None))
        if line_fixes:
            def _lines(fixes=line_fixes):
                for ln, target in fixes:
                    ln.material_name = target
                    ln.material_catalog_id = catalog[target].id
            steps.append(("PO lines", f"{len(line_fixes)} line(s) renamed", _lines))

        # ---- planned materials on job orders -------------------------------------
        planned = Counter()
        job_fixes = []
        for job in JobOrder.query.filter(db.func.jsonb_array_length(JobOrder.raw_materials) > 0).all():
            items = [dict(m) for m in (job.raw_materials or [])]
            changed = False
            for m in items:
                target = _target(m.get("name"), m.get("sizeSpec"), on_list)
                if target:
                    planned[(m.get("name"), target)] += 1
                    m["name"] = target
                    m["materialCatalogId"] = catalog[target].id
                    changed = True
            if changed:
                job_fixes.append((job, items))
        for (old, new), n in sorted(planned.items()):
            steps.append(("Job orders", f"planned {old} → {new} ({n})", None))
        if job_fixes:
            def _jobs(fixes=job_fixes):
                for job, items in fixes:
                    job.raw_materials = items
                    flag_modified(job, "raw_materials")
            steps.append(("Job orders", f"{len(job_fixes)} job order(s) updated", _jobs))

        # ---- catalog entries left with nothing --------------------------------------
        moving = {ln.id for ln, _ in line_fixes}
        for item in Catalog.query.order_by(Catalog.name).all():
            if on_list(item.name):
                continue
            still = [
                p.id for p in MaterialPurchase.query.filter(MaterialPurchase.material_catalog_id == item.id).all()
                if p.id not in moving
            ]
            if still:
                steps.append(("Catalog", f"keep {item.name}: {len(still)} line(s) not on the map", None))
                continue
            steps.append(("Catalog", f"delete {item.name}", lambda i=item: db.session.delete(i)))

        # ---- consumables --------------------------------------------------------------
        sample = {
            (r.table_name, r.record_id)
            for r in SampleRecord.query.filter(
                SampleRecord.table_name.in_(["tool_events", "consumable_purchases", "consumable_requests"])
            ).all()
        }
        for tool in Tool.query.filter_by(category=ToolCategory.CONSUMABLE).order_by(Tool.name).all():
            if match(CONSUMABLES, tool.name):
                continue
            target = CONSUMABLE_MAP.get(" ".join(tool.name.lower().split()))
            if target:
                size = " ".join(filter(None, [tool.name, tool.size_spec]))
                text = f"{tool.name} → {target} (size {size})"
                def _join(t=tool, n=target, z=size):
                    t.size_spec = z
                    t.name = n
                    t.active = True
                steps.append(("Consumables", text, _join))
                continue
            events = ToolEvent.query.filter(ToolEvent.tool_id == tool.id).all()
            buys = ConsumablePurchase.query.filter(ConsumablePurchase.tool_id == tool.id).all()
            asks = ConsumableRequest.query.filter(ConsumableRequest.tool_id == tool.id).all()
            counts = StocktakeLine.query.filter(StocktakeLine.tool_id == tool.id).count()
            po = MaterialPurchase.query.filter(MaterialPurchase.tool_id == tool.id).count()
            real = (
                sum(1 for e in events if ("tool_events", str(e.id)) not in sample)
                + sum(1 for b in buys if ("consumable_purchases", str(b.id)) not in sample)
                + sum(1 for a in asks if ("consumable_requests", str(a.id)) not in sample)
                + counts + po
            )
            if real:
                steps.append(("Consumables", f"keep {tool.name}: {real} record(s) that are not sample data", None))
                continue

            def _drop(t=tool, es=events, bs=buys, rs=asks):
                ids = [e.id for e in es]
                if ids:
                    ToolEvent.query.filter(ToolEvent.undoes_event_id.in_(ids)).update(
                        {ToolEvent.undoes_event_id: None}, synchronize_session=False)
                    SampleRecord.query.filter(SampleRecord.table_name == "tool_events",
                                              SampleRecord.record_id.in_([str(i) for i in ids])).delete(
                        synchronize_session=False)
                    ToolEvent.query.filter(ToolEvent.id.in_(ids)).delete(synchronize_session=False)
                # Requests point at purchases, so they go first.
                for table, rows, model in (("consumable_requests", rs, ConsumableRequest),
                                           ("consumable_purchases", bs, ConsumablePurchase)):
                    rid = [r.id for r in rows]
                    if rid:
                        SampleRecord.query.filter(SampleRecord.table_name == table,
                                                  SampleRecord.record_id.in_([str(i) for i in rid])).delete(
                            synchronize_session=False)
                        model.query.filter(model.id.in_(rid)).delete(synchronize_session=False)
                SampleRecord.query.filter(SampleRecord.table_name == "tools",
                                          SampleRecord.record_id == str(t.id)).delete(synchronize_session=False)
                db.session.delete(t)
            steps.append(("Consumables",
                          f"delete {tool.name}{' ' + tool.size_spec if tool.size_spec else ''}: "
                          f"{len(events)} sample stock events, {len(buys)} purchases, {len(asks)} requests",
                          _drop))

        changes = [s for s in steps if s[2] is not None]
        for kind in ("PO lines", "Job orders", "Catalog", "Consumables"):
            mine = [s for s in steps if s[0] == kind]
            print(f"\n{kind}:")
            if not mine:
                print("  (nothing to change)")
            for _, text, _fn in mine:
                print(f"  {text}")
        print(f"\n{len(changes)} change(s).")
        if not args.apply:
            print("Preview: nothing changed. Run again with --apply --expect N to save.")
            return
        if args.expect is None or args.expect != len(changes):
            raise SystemExit(f"Found {len(changes)}, --expect was {args.expect}. Nothing changed.")
        confirm_database(label)
        try:
            for _, _, fn in changes:
                fn()
                db.session.flush()
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise SystemExit(f"Nothing changed: {exc}")
        print(f"Saved {len(changes)} change(s).")


if __name__ == "__main__":
    main()
