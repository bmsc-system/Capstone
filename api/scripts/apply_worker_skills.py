"""Set workers' machine skills (level 1-5, primary) from a CSV file.

The CSV has a header row: name,machine,level,primary
  name     the worker's full name as in Users & Roles
  machine  the machine type name or code (Lathe, Milling, ...)
  level    1-5
  primary  yes for the worker's main machine (one per worker), else blank

Only the listed skills are added or updated. Skills not in the file are left
as they are, and machine units keep their assigned operators. Names stay out
of the repository: keep the CSV on your own computer.

Preview (changes nothing), then apply, from the api/ folder:
    .venv\\Scripts\\python.exe scripts\\apply_worker_skills.py --csv C:\\path\\skills.csv
    .venv\\Scripts\\python.exe scripts\\apply_worker_skills.py --csv C:\\path\\skills.csv --apply

You are asked for the database URL at a hidden prompt; press Enter to use
DATABASE_URL from the environment / api/.env instead.
"""

from __future__ import annotations

import argparse
import csv

from _target_db import confirm_database, name_key, open_app


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = {"name", "machine", "level"} - {(h or "").strip().lower() for h in reader.fieldnames or []}
        if missing:
            raise SystemExit(f"The CSV needs the columns name, machine, level (missing: {', '.join(sorted(missing))}).")
        rows = []
        for line_no, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if not row.get("name"):
                continue
            try:
                level = int(row["level"])
            except ValueError:
                raise SystemExit(f"Line {line_no}: level must be 1-5.")
            if not 1 <= level <= 5:
                raise SystemExit(f"Line {line_no}: level must be 1-5.")
            rows.append(
                {
                    "line": line_no,
                    "name": row["name"],
                    "machine": row["machine"],
                    "level": level,
                    "primary": row.get("primary", "").lower() in ("yes", "y", "true", "1", "primary"),
                }
            )
        return rows


def plan(rows):
    """(changes, problems) without writing anything."""
    from app.models.machine import MachineType
    from app.models.user import User, UserRole, UserStatus
    from app.models.worker_skill import WorkerSkill

    workers = {}
    for user in User.query.filter(
        User.role == UserRole.PRODUCTION_WORKER, User.status != UserStatus.DISABLED
    ).all():
        workers.setdefault(name_key(user.full_name), []).append(user)
    machines = {}
    for mt in MachineType.query.all():
        machines[name_key(mt.name)] = mt
        if mt.code:
            machines.setdefault(name_key(mt.code), mt)

    changes, problems = [], []
    primaries = {}
    for row in rows:
        found = workers.get(name_key(row["name"]), [])
        mt = machines.get(name_key(row["machine"]))
        if len(found) != 1:
            problems.append(
                f"Line {row['line']}: {row['name']} — "
                + ("no active production worker by that name" if not found else "more than one worker by that name")
            )
            continue
        if mt is None:
            problems.append(f"Line {row['line']}: no machine type called {row['machine']}")
            continue
        worker = found[0]
        if row["primary"]:
            if primaries.get(worker.id):
                problems.append(f"Line {row['line']}: {worker.full_name} has more than one primary machine")
                continue
            primaries[worker.id] = mt.id
        existing = WorkerSkill.query.filter_by(worker_id=worker.id, machine_type_id=mt.id).first()
        changes.append(
            {
                "worker": worker,
                "machine": mt,
                "level": row["level"],
                "primary": row["primary"],
                "before": (existing.proficiency, existing.is_primary) if existing else None,
            }
        )
    return changes, problems, primaries


def apply(changes, primaries):
    from app.extensions import db
    from app.models.worker_skill import WorkerSkill
    from app.services.worker_profile_service import ensure_worker_profile

    for worker_id, machine_id in primaries.items():
        for skill in WorkerSkill.query.filter_by(worker_id=worker_id).all():
            if skill.machine_type_id != machine_id and skill.is_primary:
                skill.is_primary = False
    for change in changes:
        worker, mt = change["worker"], change["machine"]
        ensure_worker_profile(worker)
        skill = WorkerSkill.query.filter_by(worker_id=worker.id, machine_type_id=mt.id).first()
        if skill is None:
            skill = WorkerSkill(worker_id=worker.id, machine_type_id=mt.id)
            db.session.add(skill)
        skill.proficiency = change["level"]
        if change["primary"]:
            skill.is_primary = True
        elif skill.is_primary is None:
            skill.is_primary = False
    db.session.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", required=True, help="Path to the skills CSV")
    parser.add_argument("--apply", action="store_true", help="Save the changes")
    args = parser.parse_args()
    rows = read_rows(args.csv)

    app, label = open_app()
    from app.extensions import db

    with app.app_context():
        print(f"Target database: {label}")
        changes, problems, primaries = plan(rows)
        for c in sorted(changes, key=lambda c: (c["worker"].full_name, c["machine"].name)):
            before = "new" if c["before"] is None else f"was {c['before'][0]}{' primary' if c['before'][1] else ''}"
            print(
                f"  {c['worker'].full_name:<24} {c['machine'].name:<12} level {c['level']}"
                f"{' primary' if c['primary'] else '':<8} ({before})"
            )
        for p in problems:
            print(f"  PROBLEM {p}")
        print(f"{len(changes)} skill(s) to set, {len(problems)} problem(s).")
        if not args.apply:
            print("Preview: nothing changed. Run again with --apply to save.")
            return
        if problems:
            raise SystemExit("Fix the problems in the CSV first. Nothing changed.")
        confirm_database(label)
        try:
            apply(changes, primaries)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise SystemExit(f"Nothing changed: {getattr(exc, 'message', None) or exc}")
        print(f"Saved {len(changes)} skill(s). Machine units kept their assigned operators.")


if __name__ == "__main__":
    main()
