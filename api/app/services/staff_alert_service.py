"""Staff alerts behind the header bell.

``raise_alert`` is the single entry point for new alert kinds: pick the roles
that should see it, link the job or supplier order, and pass a dedupe key when
a repeating check (for example a daily overdue scan) must not raise the same
alert twice. Callers commit.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.models.staff_alert import StaffAlert, StaffAlertKind
from app.models.user import User, UserRole
from app.utils.errors import AppError

LIST_LIMIT = 50


def _recipients(roles):
    return (
        User.query.filter(User.role.in_(list(roles)), User.active.is_(True))
        .with_entities(User.id)
        .all()
    )


def raise_alert(
    *,
    roles,
    kind: str,
    title: str,
    message: str | None = None,
    job_order_id: str | None = None,
    supplier_order_id: str | None = None,
    dedupe_key: str | None = None,
) -> list[StaffAlert]:
    """One alert per active user in ``roles``. Does not commit."""
    existing = set()
    if dedupe_key:
        existing = {
            rid
            for (rid,) in db.session.query(StaffAlert.recipient_id).filter(
                StaffAlert.dedupe_key == dedupe_key
            )
        }
    created = []
    for (user_id,) in _recipients(roles):
        if user_id in existing:
            continue
        alert = StaffAlert(
            recipient_id=user_id,
            kind=kind,
            title=title[:200],
            message=message,
            job_order_id=job_order_id,
            supplier_order_id=supplier_order_id,
            dedupe_key=dedupe_key,
        )
        db.session.add(alert)
        created.append(alert)
    return created


def material_delay_alert(job, outcome: dict, dedupe_key: str | None = None) -> list[StaffAlert]:
    """Tell the Admin a job was moved later for materials (one per delay)."""
    return raise_alert(
        roles=[UserRole.ADMIN],
        kind=StaffAlertKind.MATERIAL_DELAY,
        title=f"{job.job_number or job.title} moved later for materials",
        message=job.material_delay_reason,
        job_order_id=job.id,
        supplier_order_id=job.material_delay_supplier_order_id,
        dedupe_key=dedupe_key,
    )


def _mine(user_id):
    return StaffAlert.query.filter(StaffAlert.recipient_id == user_id)


def unread_count(user_id) -> int:
    return _mine(user_id).filter(StaffAlert.read_at.is_(None)).count()


def list_alerts(user_id, limit: int = LIST_LIMIT, offset: int = 0, unread_only: bool = False) -> dict:
    """Newest first. The bell asks for the latest page; the Notifications page
    pages through everything (``offset``) or only the unread ones."""
    limit = max(1, min(int(limit or LIST_LIMIT), 200))
    offset = max(0, int(offset or 0))
    query = _mine(user_id)
    if unread_only:
        query = query.filter(StaffAlert.read_at.is_(None))
    total = query.count()
    items = (
        query.order_by(StaffAlert.created_at.desc(), StaffAlert.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [a.to_dict() for a in items],
        "unreadCount": unread_count(user_id),
        "total": total,
        "hasMore": offset + len(items) < total,
    }


def mark_read(user_id, alert_id) -> dict:
    alert = _mine(user_id).filter(StaffAlert.id == alert_id).first()
    if alert is None:
        raise AppError("Notification not found", "NOT_FOUND", 404)
    if alert.read_at is None:
        alert.read_at = datetime.now(timezone.utc)
        db.session.commit()
    return alert.to_dict()


def mark_all_read(user_id) -> int:
    count = (
        _mine(user_id)
        .filter(StaffAlert.read_at.is_(None))
        .update({"read_at": datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.session.commit()
    return count
