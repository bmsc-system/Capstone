"""Give a production worker some of the upcoming scheduled work (demo data).

Picks released job orders (Scheduled or In progress) whose remaining
operations have not started yet and makes the worker the lead on those
operations, at the times they are already booked, so the worker's
My Assignments has real jobs to open, start and finish. Machine bookings and
machine units' assigned operators are not touched; helpers stay on. Jobs the
worker could start right away (earlier operations done, materials in) are
picked first; a job is skipped when its times would clash with work the
worker already has.

When the worker has no working hours set, they get the hours of the first
replaced lead, so the bookings fall inside their day.

Preview (changes nothing), then apply, from the api/ folder:
    .venv\\Scripts\\python.exe scripts\\give_worker_jobs.py --email worker@example.com
    .venv\\Scripts\\python.exe scripts\\give_worker_jobs.py --email worker@example.com --jobs 3 --apply

You are asked for the database URL at a hidden prompt; press Enter to use
DATABASE_URL from the environment / api/.env instead.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from _target_db import confirm_database, open_app


def _overlaps(start, end, busy):
    return any(start < b_end and b_start < end for b_start, b_end in busy)


def plan(worker, count):
    from app.models.job_order import JobOrder, JobOrderStatus
    from app.models.operation import JobOperation, OperationStatus
    from app.services import material_purchase_service as mp_service

    now = datetime.now(timezone.utc)
    busy = [
        (op.scheduled_start, op.scheduled_end)
        for op in JobOperation.query.filter(
            JobOperation.crew_includes(worker.id),
            JobOperation.status != OperationStatus.COMPLETED,
            JobOperation.scheduled_start.isnot(None),
            JobOperation.scheduled_end.isnot(None),
        ).all()
    ]

    candidates = []
    jobs = JobOrder.query.filter(
        JobOrder.status.in_((JobOrderStatus.SCHEDULED, JobOrderStatus.IN_PROGRESS)),
        JobOrder.archived_at.is_(None),
    ).all()
    for job in jobs:
        ops = sorted(job.operations or [], key=lambda o: o.sequence_no or 0)
        todo = [
            o
            for o in ops
            if o.status in (OperationStatus.PENDING, OperationStatus.SCHEDULED)
            and not o.is_outsourced
            and o.scheduled_start is not None
            and o.scheduled_end is not None
            and o.scheduled_end > now
        ]
        if not todo or any(worker.id in o.crew_ids for o in todo):
            continue
        next_op = next((o for o in ops if o.status != OperationStatus.COMPLETED), None)
        ready = next_op is not None and next_op in todo and (
            mp_service.job_has_started(job)
            or mp_service.material_start_block(job, for_worker=True) is None
        )
        candidates.append((not ready, min(o.scheduled_start for o in todo), job, todo, ready))

    candidates.sort(key=lambda c: (c[0], c[1]))
    chosen = []
    for _, _, job, todo, ready in candidates:
        if len(chosen) >= count:
            break
        if any(_overlaps(o.scheduled_start, o.scheduled_end, busy) for o in todo):
            continue
        chosen.append((job, todo, ready))
        busy.extend((o.scheduled_start, o.scheduled_end) for o in todo)
    return chosen


def copy_hours_if_missing(worker, from_worker_id):
    """Give the worker the replaced lead's weekly hours when they have none."""
    from app.extensions import db
    from app.models.worker_skill import WorkerSchedule
    from app.services.schedule_calendar import has_working_hours, load_worker_schedule_maps

    if has_working_hours(load_worker_schedule_maps(worker.id)) or not from_worker_id:
        return False
    source = WorkerSchedule.query.filter_by(worker_id=from_worker_id).all()
    if not source:
        return False
    WorkerSchedule.query.filter_by(worker_id=worker.id).delete()
    for row in source:
        db.session.add(
            WorkerSchedule(
                worker_id=worker.id,
                day_of_week=row.day_of_week,
                start_time=row.start_time,
                end_time=row.end_time,
                is_working=row.is_working,
            )
        )
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="The production worker's email")
    parser.add_argument("--jobs", type=int, default=3, help="How many job orders to give (default 3)")
    parser.add_argument("--apply", action="store_true", help="Save the changes")
    args = parser.parse_args()
    if args.jobs < 1:
        raise SystemExit("--jobs must be at least 1.")

    app, label = open_app()
    from sqlalchemy import func

    from app.extensions import db
    from app.models.user import User, UserRole, UserStatus
    from app.services.schedule_calendar import utc_to_shop

    with app.app_context():
        print(f"Target database: {label}")
        worker = User.query.filter(func.lower(User.email) == args.email.strip().lower()).first()
        if worker is None:
            raise SystemExit(f"No account with the email {args.email}.")
        if worker.role != UserRole.PRODUCTION_WORKER:
            raise SystemExit(f"{worker.full_name} is not a Production Worker.")
        if worker.status == UserStatus.DISABLED:
            raise SystemExit(f"{worker.full_name} is disabled. Re-enable the account first.")

        chosen = plan(worker, args.jobs)
        if not chosen:
            raise SystemExit("No upcoming job fits this worker's free time. Nothing changed.")
        print(f"Worker: {worker.full_name}")
        for job, todo, ready in chosen:
            print(f"  {job.job_number}  {job.title}  ({job.client.name if job.client else '-'})"
                  f"{'  - can start now' if ready else ''}")
            for op in todo:
                was = op.assigned_worker.full_name if op.assigned_worker else "nobody"
                print(
                    f"      #{op.sequence_no} {op.operation_name or 'Operation':<22}"
                    f" {utc_to_shop(op.scheduled_start):%b %d %I:%M %p} - {utc_to_shop(op.scheduled_end):%b %d %I:%M %p}"
                    f"  (lead was {was})"
                )
        ops_total = sum(len(todo) for _, todo, _ in chosen)
        print(f"{len(chosen)} job order(s), {ops_total} operation(s) to give.")
        if not args.apply:
            print("Preview: nothing changed. Run again with --apply to save.")
            return

        confirm_database(label)
        try:
            first_lead = chosen[0][1][0].assigned_worker_id
            hours_copied = copy_hours_if_missing(worker, first_lead)
            for _, todo, _ in chosen:
                for op in todo:
                    op.set_helpers([h for h in op.helper_ids if h != worker.id])
                    op.assigned_worker_id = worker.id
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise SystemExit(f"Nothing changed: {getattr(exc, 'message', None) or exc}")
        print(f"Saved: {worker.full_name} now leads {ops_total} operation(s) on {len(chosen)} job order(s).")
        if hours_copied:
            print("Their working hours were empty, so they got the replaced lead's hours (see Worker setup).")


if __name__ == "__main__":
    main()
