"""Fill in the hours worked of completed operations that were saved without them.

Until the fix in operation_service._append_log, completing an operation from
the app counted the hours worked before the completion entry was in the list,
so the operation was saved with no hours worked and no difference from target.
This recounts those operations from their time log, the same way the app does
now. Operations that already have hours worked are left alone.

Preview (changes nothing):
    .venv\\Scripts\\python.exe scripts\\backfill_worked_hours.py

Save, refusing unless the number of operations equals --expect:
    .venv\\Scripts\\python.exe scripts\\backfill_worked_hours.py --apply --expect N
"""

import argparse

from _target_db import confirm_database, open_app


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Save the changes")
    parser.add_argument("--expect", type=int, help="Number of operations the preview showed")
    args = parser.parse_args()

    app, label = open_app()

    from app.extensions import db
    from app.models.operation import JobOperation, OperationStatus
    from app.services.operation_service import compute_worked_hours, recompute_variance

    with app.app_context():
        print(f"Target database: {label}")
        ops = (
            JobOperation.query.filter(
                JobOperation.status == OperationStatus.COMPLETED,
                JobOperation.actual_worked_hours.is_(None),
            )
            .order_by(JobOperation.actual_end)
            .all()
        )
        fixable = [(op, compute_worked_hours(op)) for op in ops]
        fixable = [(op, h) for op, h in fixable if h is not None]
        for op, hours in fixable:
            job = op.job_order.job_number if op.job_order else "-"
            print(f"  {job}  #{op.sequence_no} {op.operation_name or 'Operation':<22} {float(hours):.2f} h")
        print(f"{len(fixable)} operation(s) to fill in ({len(ops) - len(fixable)} without a usable time log).")
        if not args.apply:
            print("Preview: nothing changed. Run again with --apply --expect N to save.")
            return
        if args.expect is None or args.expect != len(fixable):
            raise SystemExit(f"Found {len(fixable)}, --expect was {args.expect}. Nothing changed.")

        confirm_database(label)
        try:
            for op, _ in fixable:
                recompute_variance(op)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise SystemExit(f"Nothing changed: {exc}")
        print(f"Filled in the hours worked of {len(fixable)} operation(s).")


if __name__ == "__main__":
    main()
