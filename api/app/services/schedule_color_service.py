"""Gives each new job order its own color on the schedule board."""

from collections import Counter

from app.extensions import db
from app.models.job_order import (
    AUTO_SCHEDULE_COLORS,
    JobOrder,
    JobOrderStatus,
    fallback_schedule_color,
)

# Jobs whose bars can still sit on the board next to a new job.
_OPEN_STATUSES = (
    JobOrderStatus.DRAFT,
    JobOrderStatus.SCHEDULED,
    JobOrderStatus.IN_PROGRESS,
)


def _least_used(used: Counter) -> str:
    return min(AUTO_SCHEDULE_COLORS, key=lambda c: (used[c], AUTO_SCHEDULE_COLORS.index(c)))


def next_schedule_color() -> str:
    """The color fewest open jobs use, so jobs on the board look different."""
    rows = (
        db.session.query(JobOrder.id, JobOrder.schedule_color)
        .filter(JobOrder.status.in_(_OPEN_STATUSES))
        .all()
    )
    used = Counter((color or fallback_schedule_color(job_id)).upper() for job_id, color in rows)
    return _least_used(used)


def assign_missing_colors() -> int:
    """Give open jobs without a saved color one of their own. Returns how many changed."""
    jobs = JobOrder.query.filter(JobOrder.status.in_(_OPEN_STATUSES)).order_by(JobOrder.created_at).all()
    used = Counter(j.schedule_color.upper() for j in jobs if j.schedule_color)
    changed = 0
    for job in jobs:
        if job.schedule_color:
            continue
        job.schedule_color = _least_used(used)
        used[job.schedule_color] += 1
        changed += 1
    db.session.commit()
    return changed
