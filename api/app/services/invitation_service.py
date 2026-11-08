"""Invitation issue/accept and password setup (email delivery only)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app
from flask_jwt_extended import create_access_token, create_refresh_token

from app.extensions import bcrypt, db
from app.models.notification import NotificationChannel, NotificationEvent
from app.models.user import User, UserRole, UserStatus
from app.models.user_security import InvitationChannel, UserInvitation
from app.services.audit_service import write_audit_event
from app.services.notification_service import send_notification
from app.utils.errors import AppError
from app.utils.passwords import validate_password
from app.utils.phone import looks_like_email, looks_like_mobile, normalize_ph_mobile

logger = logging.getLogger(__name__)

INVITE_TTL = timedelta(hours=48)


def _utcnow():
    return datetime.now(timezone.utc)


def _pepper() -> str:
    return current_app.config.get("INVITATION_TOKEN_PEPPER") or current_app.config["SECRET_KEY"]


def hash_invite_secret(raw: str) -> str:
    return hmac.new(_pepper().encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def invitation_setup_url(raw_secret: str) -> str:
    from app.services.email_templates import absolute_frontend_url

    return absolute_frontend_url(f"/set-password?token={raw_secret}")


def _invalidate_open_invites(user_id: str) -> int:
    now = _utcnow()
    open_invites = UserInvitation.query.filter(
        UserInvitation.user_id == user_id,
        UserInvitation.used_at.is_(None),
        UserInvitation.revoked_at.is_(None),
    ).all()
    for inv in open_invites:
        inv.revoked_at = now
    return len(open_invites)


def revoke_open_invites(user: User, *, reason: str) -> int:
    """Revoke every unused setup link of the user without committing."""
    count = _invalidate_open_invites(user.id)
    if count:
        write_audit_event(
            "INVITATION_REVOKED",
            "User",
            user.id,
            after={"count": count, "reason": reason},
        )
    return count


def create_invited_user(
    *,
    full_name: str,
    email: str,
    mobile_number: str,
    role: UserRole,
    channel: InvitationChannel | None = None,
    created_by_id: str,
    nickname: str | None = None,
) -> tuple[User, UserInvitation, str]:
    # Every account has an email: the setup link is sent there. The mobile
    # number is optional and can also be used to sign in.
    email_norm = (email or "").strip().lower() or None
    if email_norm is None or "@" not in email_norm:
        raise AppError("Valid email is required", "VALIDATION_ERROR", 400)
    if User.query.filter_by(email=email_norm).first():
        raise AppError("Email already exists", "CONFLICT", 409)

    mobile = normalize_ph_mobile(mobile_number, required=False)
    if mobile and User.query.filter_by(mobile_number=mobile).first():
        raise AppError("Mobile number already exists", "CONFLICT", 409)

    if channel is not None and channel != InvitationChannel.EMAIL:
        raise AppError(
            "Invitations are sent by email only",
            "VALIDATION_ERROR",
            400,
        )

    user = User(
        email=email_norm,
        mobile_number=mobile,
        password_hash=None,
        full_name=full_name.strip(),
        nickname=(nickname or "").strip()[:40] or None,
        role=role,
        status=UserStatus.INVITED,
        active=False,
    )
    db.session.add(user)
    db.session.flush()

    from app.services.worker_profile_service import ensure_worker_profile

    ensure_worker_profile(user)

    invitation, raw_secret = issue_invitation(
        user, InvitationChannel.EMAIL, created_by_id, commit=False
    )
    db.session.commit()
    invitation.delivery = _deliver_invitation(user, invitation, raw_secret)
    invitation.setup_url = invitation_setup_url(raw_secret)
    return user, invitation, raw_secret


def issue_invitation(
    user: User,
    channel: InvitationChannel | None = None,
    created_by_id: str | None = None,
    *,
    commit: bool = True,
    deliver: bool = True,
) -> tuple[UserInvitation, str]:
    if user.status == UserStatus.DISABLED:
        raise AppError("Cannot invite a disabled user", "VALIDATION_ERROR", 400)
    if channel is not None and channel != InvitationChannel.EMAIL:
        raise AppError(
            "Invitations are sent by email only",
            "VALIDATION_ERROR",
            400,
        )
    if not user.email:
        raise AppError(
            "Add an email before sending the setup link",
            "VALIDATION_ERROR",
            400,
        )

    _invalidate_open_invites(user.id)

    raw_secret = secrets.token_urlsafe(32)
    invitation = UserInvitation(
        user_id=user.id,
        token_hash=hash_invite_secret(raw_secret),
        channel=InvitationChannel.EMAIL,
        expires_at=_utcnow() + INVITE_TTL,
        created_by_id=created_by_id,
    )
    db.session.add(invitation)
    if user.status != UserStatus.ACTIVE:
        user.status = UserStatus.INVITED
        user.sync_active_flag()

    write_audit_event(
        "INVITATION_SENT",
        "UserInvitation",
        None,
        after={"userId": user.id, "channel": InvitationChannel.EMAIL.value},
    )
    if commit:
        db.session.commit()
        # Not a column: the outcome of this send, for the admin's response.
        # deliver=False is the copy-link fallback: same link rules (48 hours,
        # single use, a new link revokes the previous one), nothing emailed.
        invitation.delivery = (
            _deliver_invitation(user, invitation, raw_secret) if deliver else None
        )
        invitation.setup_url = invitation_setup_url(raw_secret)
    else:
        db.session.flush()
    return invitation, raw_secret


def resend_invitation(
    user: User,
    created_by_id: str,
    channel: InvitationChannel | None = None,
    *,
    deliver: bool = True,
):
    if user.status == UserStatus.ACTIVE and user.password_hash:
        raise AppError("User already activated", "VALIDATION_ERROR", 400)
    if user.status == UserStatus.DISABLED:
        raise AppError("User is disabled", "VALIDATION_ERROR", 400)
    if channel is not None and channel != InvitationChannel.EMAIL:
        raise AppError(
            "Invitations are sent by email only",
            "VALIDATION_ERROR",
            400,
        )
    return issue_invitation(
        user, InvitationChannel.EMAIL, created_by_id, commit=True, deliver=deliver
    )


def revoke_invitation(user: User) -> int:
    now = _utcnow()
    open_invites = UserInvitation.query.filter(
        UserInvitation.user_id == user.id,
        UserInvitation.used_at.is_(None),
        UserInvitation.revoked_at.is_(None),
    ).all()
    for inv in open_invites:
        inv.revoked_at = now
    write_audit_event(
        "INVITATION_REVOKED",
        "User",
        user.id,
        after={"count": len(open_invites)},
    )
    db.session.commit()
    return len(open_invites)


def _deliver_invitation(user: User, invitation: UserInvitation, raw_secret: str):
    """Runs after the invitation is committed. Returns the notification log row
    (None if even that failed); delivery problems are logged, never raised."""
    log = None
    try:
        from app.services.email_templates import invitation_email

        link = invitation_setup_url(raw_secret)
        content = invitation_email(user.full_name or "", link)
        log = send_notification(
            channel=NotificationChannel.EMAIL,
            recipient=user.email,
            event=NotificationEvent.INVITATION,
            subject=content.subject,
            body=content.text,
            html_body=content.html,
            store_body=False,
        )
        # Never include the raw token/code — safe for production logs.
        logger.info(
            "Invitation delivery user_id=%s to=%s channel=%s status=%s",
            user.id,
            user.email,
            invitation.channel.value,
            log.status.value if log else "NOT_LOGGED",
        )
    except Exception:  # noqa: BLE001 - the invitation itself is already saved
        logger.exception("Invitation delivery failed user_id=%s", user.id)
    return log


def _find_invitation_by_secret(raw_secret: str) -> UserInvitation | None:
    if not raw_secret:
        return None
    token_hash = hash_invite_secret(raw_secret.strip())
    return UserInvitation.query.filter_by(token_hash=token_hash).first()


def _user_matches_identifier(user: User, identifier: str | None) -> bool:
    raw = (identifier or "").strip()
    if not raw:
        return False
    if looks_like_email(raw):
        return user.email == raw.lower()
    if looks_like_mobile(raw):
        try:
            return user.mobile_number == normalize_ph_mobile(raw, required=True)
        except AppError:
            return False
    if user.email == raw.lower():
        return True
    try:
        return user.mobile_number == normalize_ph_mobile(raw, required=True)
    except AppError:
        return False


def _require_open_invitation(
    raw_secret: str, identifier: str | None = None
) -> tuple[UserInvitation, User]:
    invitation = _find_invitation_by_secret(raw_secret)
    if not invitation:
        raise AppError("Invalid invitation", "INVITE_INVALID", 400)
    if invitation.used_at is not None:
        raise AppError("This invitation was already used", "INVITE_USED", 400)
    if invitation.revoked_at is not None:
        raise AppError("This invitation was revoked", "INVITE_REVOKED", 400)
    exp = invitation.expires_at
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp is None or exp <= _utcnow():
        raise AppError("This invitation has expired", "INVITE_EXPIRED", 400)
    user = User.query.get(invitation.user_id)
    if not user or user.status == UserStatus.DISABLED:
        raise AppError("Invalid invitation", "INVITE_INVALID", 400)
    # Legacy SMS invites still require identifier; email-link invites are optional.
    if invitation.channel == InvitationChannel.SMS:
        if not identifier or not _user_matches_identifier(user, identifier):
            raise AppError("Invalid invitation", "INVITE_INVALID", 400)
    elif identifier and not _user_matches_identifier(user, identifier):
        raise AppError("Invalid invitation", "INVITE_INVALID", 400)
    return invitation, user


def validate_invitation_secret(raw_secret: str, identifier: str | None = None) -> dict:
    invitation, user = _require_open_invitation(raw_secret, identifier)
    return {
        "valid": True,
        "channel": invitation.channel.value,
        "email": user.email,
        "fullName": user.full_name,
        "expiresAt": invitation.expires_at.isoformat() if invitation.expires_at else None,
    }


def accept_invitation(
    raw_secret: str,
    password: str,
    password_confirm: str,
    identifier: str | None = None,
) -> dict:
    if password != password_confirm:
        raise AppError("Passwords do not match", "VALIDATION_ERROR", 400)
    validate_password(password)

    invitation, user = _require_open_invitation(raw_secret, identifier)

    user.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
    user.status = UserStatus.ACTIVE
    user.sync_active_flag()
    invitation.used_at = _utcnow()

    write_audit_event(
        "INVITATION_USED",
        "UserInvitation",
        invitation.id,
        after={"userId": user.id},
    )
    write_audit_event("PASSWORD_SET", "User", user.id, after={"via": "invitation"})
    db.session.commit()

    claims = {"role": user.role.value}
    return {
        "accessToken": create_access_token(identity=user.id, additional_claims=claims),
        "refreshToken": create_refresh_token(identity=user.id, additional_claims=claims),
        "user": user.to_dict(include_profile=True),
    }
