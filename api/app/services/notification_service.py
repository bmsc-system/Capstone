"""Every outbound email goes through send_notification.

Call it only after the business transaction has committed. It applies the
NOTIFICATIONS_ENABLED switch and NOTIFY_ALLOWLIST, sends through the configured
backend, writes exactly one notification_logs row, and never raises: a failed
notification must not fail or roll back the user's action.

SMS is no longer a delivery channel. Historical notification_logs rows with
channel=SMS remain readable; new sends are email only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models.job_order import JobOrder
from app.models.notification import (
    NotificationChannel,
    NotificationEvent,
    NotificationLog,
    readable_send_error,
    NotificationMilestone,
    NotificationStatus,
)
from app.services.email_backends import ConsoleEmailBackend, get_email_backend
from app.services.email_templates import job_update_email_content, test_email
from app.utils.errors import AppError
from app.utils.phone import normalize_ph_mobile

logger = logging.getLogger(__name__)

# Shown to staff as is, so in plain words (no setting names).
TURNED_OFF = "Email sending is turned off on this server"
NOT_ALLOWLISTED = "This server only sends email to approved test addresses"
OPTED_OUT = "Client opted out of email updates"
EMAIL_UPDATES_OFF = "Email updates are off for this client"
BODY_NOT_STORED = "[not stored: contains a sign-in link or code]"
SMS_RETIRED = "SMS notifications are no longer available; use email"


def _utcnow():
    return datetime.now(timezone.utc)


def _normalize_phone_for_allowlist(raw: str | None) -> str | None:
    try:
        return normalize_ph_mobile(raw)
    except AppError:
        return None


def _normalize_recipient(channel: NotificationChannel, recipient: str | None) -> str | None:
    raw = (recipient or "").strip()
    if channel == NotificationChannel.EMAIL:
        return raw.lower() if "@" in raw else None
    # SMS channel kept only for history / reject-on-send.
    return _normalize_phone_for_allowlist(raw)


def parse_allowlist(raw: str | None) -> set[str]:
    entries = set()
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "@" in item:
            entries.add(item.lower())
        else:
            entries.add(_normalize_phone_for_allowlist(item) or item)
    return entries


def _attempt(cfg, channel, to, subject, body, html_body):
    """Return (status, error, provider_reference, stored_body)."""
    if channel != NotificationChannel.EMAIL:
        return NotificationStatus.SKIPPED, SMS_RETIRED, None, body
    if not to:
        return NotificationStatus.SKIPPED, "No valid email address for this channel", None, body

    if not cfg.get("NOTIFICATIONS_ENABLED"):
        # Still printed (body only in development) so local invite links keep working.
        ConsoleEmailBackend().send(to, subject, body)
        return NotificationStatus.SKIPPED, TURNED_OFF, None, body

    allowlist = parse_allowlist(cfg.get("NOTIFY_ALLOWLIST"))
    if allowlist and to not in allowlist:
        return NotificationStatus.SKIPPED, NOT_ALLOWLISTED, None, body

    backend = get_email_backend(cfg)
    reference = backend.send(to, subject, body, html_body)
    if not backend.delivers:
        return NotificationStatus.SKIPPED, "EMAIL_BACKEND is console: logged, not sent", None, body
    return NotificationStatus.SENT, None, reference, body



def friendly_send_error(exc: Exception) -> str:
    """What Office Staff see when an email could not be sent. The technical
    error stays in the server log."""
    import smtplib
    import socket

    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The email address was refused by the mail server. Check the client's email address."
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "The shop's email account could not sign in to the mail server. Ask the Admin to check the email settings."
    if isinstance(exc, (ConnectionError, socket.timeout, TimeoutError, smtplib.SMTPConnectError,
                        smtplib.SMTPServerDisconnected, OSError)):
        return "The mail server could not be reached. Try sending again later."
    # Errors raised by the app itself (for example, missing email settings) are
    # already written for people.
    return str(exc)[:2000] or "The email could not be sent. Try sending again later."

def send_notification(
    *,
    channel: NotificationChannel,
    recipient: str | None,
    event: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    job_order_id: str | None = None,
    client_id: str | None = None,
    milestone: NotificationMilestone | None = None,
    store_body: bool = True,
    skip_reason: str | None = None,
) -> NotificationLog | None:
    """Send one message and log it. Returns the log row (None if even that failed)."""
    to = None
    stored_body = body
    try:
        to = _normalize_recipient(channel, recipient)
        if skip_reason:
            status, error, reference = NotificationStatus.SKIPPED, skip_reason, None
        else:
            status, error, reference, stored_body = _attempt(
                current_app.config, channel, to, subject, body, html_body
            )
    except Exception as exc:  # noqa: BLE001 - a notification must never break the action
        logger.exception("Notification send failed event=%s channel=%s", event, channel.value)
        status, error, reference = NotificationStatus.FAILED, friendly_send_error(exc), None

    from app.utils.log_mask import mask_email

    logger.info(
        "Notification event=%s channel=%s to=%s status=%s",
        event,
        channel.value,
        mask_email(to or recipient) or "(none)",
        status.value,
    )
    log = NotificationLog(
        job_order_id=job_order_id,
        client_id=client_id,
        milestone=milestone,
        event=event,
        channel=channel,
        recipient=(to or (recipient or "").strip() or "(none)")[:255],
        message_body=stored_body if store_body else BODY_NOT_STORED,
        status=status,
        provider_reference=(str(reference)[:255] if reference else None),
        error_message=error,
        sent_at=_utcnow() if status == NotificationStatus.SENT else None,
    )
    try:
        db.session.add(log)
        db.session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write notification log event=%s", event)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    return log


def delivery_summary(log: NotificationLog | None) -> dict:
    """What the admin is shown after an invitation: never the link or code."""
    if log is None:
        return {
            "status": "FAILED",
            "channel": None,
            "recipient": None,
            "errorMessage": "The delivery could not be recorded; check the server log",
        }
    return {
        "status": log.status.value,
        "channel": log.channel.value,
        "recipient": log.recipient,
        "errorMessage": readable_send_error(log.error_message),
    }


def _already_logged(job_id: str, milestone: NotificationMilestone, channel: NotificationChannel) -> bool:
    return (
        NotificationLog.query.filter_by(
            job_order_id=job_id,
            milestone=milestone,
            channel=channel,
        ).first()
        is not None
    )


def _sent_today(job_id: str) -> bool:
    """A job update for this job already went out today (shop time)."""
    from app.services.job_update_service import shop_day_start_utc

    return (
        NotificationLog.query.filter(
            NotificationLog.job_order_id == job_id,
            NotificationLog.milestone.isnot(None),
            NotificationLog.status == NotificationStatus.SENT,
            NotificationLog.created_at >= shop_day_start_utc(),
        ).first()
        is not None
    )


def _progress_skip_reason(job, client, milestone, progress) -> str | None:
    """Why a Started / Halfway / Final stage email is not sent, or None."""
    from app.services import job_update_service as ju

    if milestone not in ju.PROGRESS_MILESTONES:
        return None
    # Every client who gets job updates gets the detailed progress emails; the
    # old "key updates only" choice was removed, so a stored KEY is ignored.
    if ju.is_short_job(progress):
        return ju.SHORT_JOB
    try:
        if _sent_today(job.id):
            return ju.ALREADY_TODAY
    except Exception:  # noqa: BLE001 - if the check fails, send (still once per milestone)
        logger.exception("Same-day check failed job=%s", job.id)
    return None


def _send_job_update(job, client, milestone, *, opted_in, recipient, progress=None, skip_reason=None, delay=None):
    content = job_update_email_content(
        milestone, job.job_number, job.title or "", job.due_date, progress=progress, delay=delay
    )
    return send_notification(
        channel=NotificationChannel.EMAIL,
        recipient=recipient,
        event=milestone.value,
        subject=content.subject,
        body=content.text,
        html_body=content.html,
        job_order_id=job.id,
        client_id=client.id,
        milestone=milestone,
        skip_reason=(None if opted_in else OPTED_OUT) or skip_reason,
    )


def notify_job_milestone(job_id: str, milestone: NotificationMilestone) -> list[NotificationLog]:
    """Email the client once per job and milestone when they opted in and have an email.

    Progress emails (Started, Halfway, Final stage) follow the client's update
    level and are skipped for short jobs or when the client already got an
    update about the job today; the skip is logged so it is not retried.
    Delayed emails go through ``notify_client_if_delayed``. Never raises.
    """
    from app.services import job_update_service as ju

    logs: list[NotificationLog] = []
    try:
        if milestone == NotificationMilestone.JOB_DELAYED:
            return logs
        job = JobOrder.query.get(job_id)
        if not job or not job.client:
            logger.warning("notify_job_milestone: job/client missing id=%s", job_id)
            return logs
        client = job.client
        if _already_logged(job.id, milestone, NotificationChannel.EMAIL):
            return logs
        # Halfway after the final stage would be old news.
        if milestone == NotificationMilestone.JOB_HALFWAY and _already_logged(
            job.id, NotificationMilestone.JOB_FINAL_STAGE, NotificationChannel.EMAIL
        ):
            return logs
        progress = ju.job_progress(job)
        log = _send_job_update(
            job,
            client,
            milestone,
            opted_in=bool(client.notify_by_email),
            recipient=client.email,
            progress=progress,
            skip_reason=_progress_skip_reason(job, client, milestone, progress),
        )
        if log is not None:
            logs.append(log)
        if milestone == NotificationMilestone.JOB_RECEIVED and progress.get("finish"):
            # The client now knows this finish date; Delayed is sent only past it.
            _remember_finish(job, max(progress["finish"], job.due_date or progress["finish"]))
    except Exception:  # noqa: BLE001
        logger.exception("notify_job_milestone unexpected error job=%s", job_id)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
    return logs


def _remember_finish(job, finish) -> None:
    try:
        job.client_notified_finish = finish
        db.session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not record the finish date told to the client job=%s", job.id)
        db.session.rollback()


def _delayed_today(job_id: str) -> bool:
    from app.services.job_update_service import shop_day_start_utc

    return (
        NotificationLog.query.filter(
            NotificationLog.job_order_id == job_id,
            NotificationLog.milestone == NotificationMilestone.JOB_DELAYED,
            NotificationLog.created_at >= shop_day_start_utc(),
        ).first()
        is not None
    )


def notify_client_if_delayed(job) -> NotificationLog | None:
    """Email the client when the scheduled finish moves past the date required
    and past the latest date they were told. At most one Delayed email per job
    a day; sent for every update level. Never raises."""
    from app.models.job_order import JobOrderStatus
    from app.services import job_update_service as ju

    try:
        if job is None or job.status not in (JobOrderStatus.SCHEDULED, JobOrderStatus.IN_PROGRESS):
            return None
        client = job.client
        if client is None or not job.due_date:
            return None
        progress = ju.job_progress(job)
        finish = progress.get("finish")
        told = job.client_notified_finish or job.due_date
        if not finish or finish <= job.due_date or finish <= told:
            return None
        if _delayed_today(job.id):
            return None
        log = _send_job_update(
            job,
            client,
            NotificationMilestone.JOB_DELAYED,
            opted_in=bool(client.notify_by_email),
            recipient=client.email,
            progress=progress,
            delay={"reason": ju.delay_reason(job), "days": (finish - job.due_date).days},
        )
        _remember_finish(job, finish)
        return log
    except Exception:  # noqa: BLE001
        logger.exception("notify_client_if_delayed failed job=%s", getattr(job, "id", None))
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def safe_notify_job_milestone(job_id: str, milestone: NotificationMilestone) -> None:
    """Fire-and-forget wrapper: never raises."""
    try:
        notify_job_milestone(job_id, milestone)
    except Exception:  # noqa: BLE001
        logger.exception("safe_notify_job_milestone failed job=%s", job_id)


def list_notification_logs(job_order_id=None, client_id=None, status=None, limit=200):
    q = NotificationLog.query
    if job_order_id:
        q = q.filter_by(job_order_id=job_order_id)
    if client_id:
        q = q.filter_by(client_id=client_id)
    if status:
        q = q.filter_by(status=NotificationStatus(status))
    return (
        q.order_by(NotificationLog.created_at.desc())
        .limit(min(int(limit or 200), 500))
        .all()
    )


def send_test_message(channel: str | None, to: str | None) -> NotificationLog:
    """Admin test: one email through the real path (switch, allowlist, backend)."""
    choice = (channel or "").strip().lower()
    if choice and choice != "email":
        raise AppError("Only email test messages are supported", "VALIDATION_ERROR", 400)
    if not _normalize_recipient(NotificationChannel.EMAIL, to):
        raise AppError("Enter an email address", "VALIDATION_ERROR", 400)

    content = test_email()
    log = send_notification(
        channel=NotificationChannel.EMAIL,
        recipient=to,
        event=NotificationEvent.TEST,
        subject=content.subject,
        body=content.text,
        html_body=content.html,
    )
    if log is None:
        raise AppError("Could not record the test message", "NOTIFICATION_LOG_FAILED", 500)
    return log
