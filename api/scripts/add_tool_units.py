"""Give each of the shop's tools its units (one QR each), up to a count.

The shop has no count of its tools yet, so the counts below are estimates
for a shop of this size. Change any of them with --set, or later in the app
(Inventory > Tools > Add units, or a unit's Retire).

Only adds: a tool that already has as many working units (not retired) as
its count is left alone, so running it again changes nothing. Collets are
counted in sets: one unit, and one QR, per set.

Preview (changes nothing):
    .venv\\Scripts\\python.exe scripts\\add_tool_units.py

Other counts:
    .venv\\Scripts\\python.exe scripts\\add_tool_units.py --set Caliper=10 --set Clamps=12

Save, refusing unless the number of new units equals --expect:
    .venv\\Scripts\\python.exe scripts\\add_tool_units.py --apply --expect N
"""

from __future__ import annotations

import argparse

from _target_db import confirm_database, open_app

# Estimates, not counted: about one caliper per lathe, a few micrometers
# shared for close fits, hammers and clamps at the benches, and a few
# collet sets for the milling machines.
ESTIMATED_COUNTS = {
    "Caliper": 8,
    "Micrometer": 4,
    "Hammers": 6,
    "Clamps": 10,
    "Collets": 3,
}
NOTES = {"Collets": "Collet set"}


def _counts(overrides):
    counts = dict(ESTIMATED_COUNTS)
    for item in overrides:
        name, _, value = item.partition("=")
        key = next((k for k in counts if k.lower() == name.strip().lower()), None)
        if key is None:
            raise SystemExit(f"Unknown tool '{name}'. Use one of: {', '.join(counts)}")
        try:
            counts[key] = int(value)
        except ValueError:
            raise SystemExit(f"Count for {key} must be a whole number, not '{value}'")
        if counts[key] < 0:
            raise SystemExit(f"Count for {key} cannot be negative")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", action="append", default=[], metavar="TOOL=COUNT",
                        help="Use this count instead of the estimate")
    parser.add_argument("--apply", action="store_true", help="Save the changes")
    parser.add_argument("--expect", type=int, help="Number of new units the preview showed")
    args = parser.parse_args()
    counts = _counts(args.set)

    app, label = open_app()

    from app.extensions import db
    from app.models.tool_type import ToolType, ToolUnit, ToolUnitStatus
    from app.services.tool_type_service import _next_asset_codes

    with app.app_context():
        print(f"Target database: {label}")
        plan = []
        for name, want in counts.items():
            tt = ToolType.query.filter(db.func.lower(ToolType.name) == name.lower()).first()
            if tt is None:
                print(f"  {name:<11} no such tool type; run set_inventory_names.py first")
                continue
            have = sum(1 for u in tt.units or [] if u.status != ToolUnitStatus.RETIRED)
            add = max(0, want - have)
            print(f"  {name:<11} has {have}, count {want}: add {add}")
            if add:
                plan.append((tt, add))
        total = sum(n for _, n in plan)
        print(f"\n{total} unit(s) to add.")
        if not args.apply:
            print("Preview: nothing changed. Run again with --apply --expect N to save.")
            return
        if args.expect is None or args.expect != total:
            raise SystemExit(f"Found {total}, --expect was {args.expect}. Nothing changed.")
        confirm_database(label)
        try:
            for tt, add in plan:
                for code in _next_asset_codes(tt, add):
                    db.session.add(ToolUnit(tool_type_id=tt.id, asset_code=code,
                                            status=ToolUnitStatus.AVAILABLE, notes=NOTES.get(tt.name)))
                db.session.flush()
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise SystemExit(f"Nothing changed: {exc}")
        print(f"Saved {total} unit(s). Print their labels from Inventory > Tools > Print QR labels.")


if __name__ == "__main__":
    main()
