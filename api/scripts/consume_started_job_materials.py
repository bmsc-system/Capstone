"""Mark received materials used when their job has already started or finished.

A received line is used up when its job's first operation starts. Older and
sample records have lines that were never marked, so they still show under
Raw materials > On hand with a long "days waiting" although the job is done.
This marks each such line used at the job's first start (or, when the job
has no recorded start, at the day the line was received).

Lines of jobs that have not started stay on hand: that is material bought
for a job and waiting for it.

Preview (changes nothing):
    .venv\\Scripts\\python.exe scripts\\consume_started_job_materials.py

Save, refusing unless the number of lines equals --expect:
    .venv\\Scripts\\python.exe scripts\\consume_started_job_materials.py --apply --expect N
"""

from __future__ import annotations

import argparse
from datetime import datetime, time, timezone

from _target_db import confirm_database, open_app

FINISHED = ("COMPLETED", "FOR_DELIVERY", "DELIVERED")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Save the changes")
    parser.add_argument("--expect", type=int, help="Number of lines the preview showed")
    args = parser.parse_args()

    app, label = open_app()

    from app.extensions import db
    from app.models.material_purchase import MaterialPurchase

    with app.app_context():
        print(f"Target database: {label}")
        lines = (
            MaterialPurchase.query.filter(
                MaterialPurchase.date_received.isnot(None),
                MaterialPurchase.consumed_at.is_(None),
                MaterialPurchase.cancelled_at.is_(None),
                MaterialPurchase.job_order_id.isnot(None),
            )
            .order_by(MaterialPurchase.date_received)
            .all()
        )
        todo = []
        waiting = 0
        for line in lines:
            job = line.job_order
            starts = [op.actual_start for op in (job.operations or []) if op.actual_start]
            status = job.status.value if job.status else ""
            if not starts and status not in FINISHED:
                waiting += 1
                continue
            received = datetime.combine(line.date_received, time(0), tzinfo=timezone.utc)
            when = max(min(starts), received) if starts else received
            todo.append((line, when, status))

        for line, when, status in todo:
            print(f"  {line.material_name:<28} received {line.date_received}  job {status:<12} used {when.date()}")
        print(f"\n{len(todo)} line(s) to mark used. {waiting} line(s) stay on hand (job not started).")
        if not args.apply:
            print("Preview: nothing changed. Run again with --apply --expect N to save.")
            return
        if args.expect is None or args.expect != len(todo):
            raise SystemExit(f"Found {len(todo)}, --expect was {args.expect}. Nothing changed.")
        confirm_database(label)
        try:
            for line, when, _ in todo:
                line.consumed_at = when
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise SystemExit(f"Nothing changed: {exc}")
        print(f"Saved {len(todo)} line(s).")


if __name__ == "__main__":
    main()
