"""PIN unlock (optional convenience after password login).

A worker has one PIN for the account, like a mobile wallet's MPIN: setting or
changing it on one phone changes it on every phone he is signed in on, and a
phone he signs in on later with his password picks it up. PIN unlock still
only works on a phone that has signed in with the password at least once.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.extensions import bcrypt, db
from app.models.user import User, UserRole, UserStatus
from app.models.user_security import UserDevice
from app.services.audit_service import write_audit_event
from app.services.session_service import issue_tokens
from app.utils.errors import AppError
from app.utils.passwords import PIN_LENGTH, is_pin_format, validate_pin

MAX_PIN_FAILURES = 5
# PIN unlock is a shop-floor convenience for Production Workers only. Admin and
# Office Staff always sign in with email (or mobile) and password.
PIN_ROLES = (UserRole.PRODUCTION_WORKER,)


def pin_allowed(user: User | None) -> bool:
    return bool(user) and user.role in PIN_ROLES
# Rows without a PIN and unused this long are deleted (a session lasts at most 7 days).
STALE_DEVICE_DAYS = 30


def _utcnow():
    return datetime.now(timezone.utc)


def _get_or_create_device(user_id: str, device_id: str, device_label: str | None) -> UserDevice:
    device = UserDevice.query.filter_by(user_id=user_id, device_id=device_id).first()
    if device and device.revoked_at is None:
        if device_label:
            device.device_label = device_label
        return device
    if device and device.revoked_at is not None:
        # A fresh row id, so tokens from before the sign-out stay rejected.
        db.session.delete(device)
        db.session.flush()
    device = UserDevice(
        user_id=user_id,
        device_id=device_id,
        device_label=device_label,
        pin_failed_attempts=0,
    )
    db.session.add(device)
    db.session.flush()
    return device


def _account_pin(user_id: str, exclude_row_id: str | None = None) -> UserDevice | None:
    """The newest PIN on any of the user's signed-in phones."""
    q = UserDevice.query.filter(
        UserDevice.user_id == user_id,
        UserDevice.revoked_at.is_(None),
        UserDevice.pin_hash.isnot(None),
    )
    if exclude_row_id:
        q = q.filter(UserDevice.id != exclude_row_id)
    return q.order_by(UserDevice.pin_set_at.desc().nullslast()).first()


def register_device_after_password_login(
    user: User, device_id: str | None, device_label: str | None = None
) -> UserDevice | None:
    if not device_id:
        return None
    prune_stale_devices(user.id)
    device = _get_or_create_device(user.id, device_id, device_label)
    device.last_used_at = _utcnow()
    if not pin_allowed(user) and device.pin_hash:
        # A PIN set before PIN unlock became worker-only stops working.
        device.pin_hash = None
        device.pin_set_at = None
        device.pin_failed_attempts = 0
    elif pin_allowed(user) and not device.pin_hash:
        # The worker's PIN works here too.
        source = _account_pin(user.id, exclude_row_id=device.id)
        if source:
            device.pin_hash = source.pin_hash
            device.pin_set_at = source.pin_set_at
            device.pin_failed_attempts = 0
    db.session.commit()
    return device


def set_device_pin(
    user: User, device_id: str, pin: str, device_label: str | None = None
) -> UserDevice:
    if not device_id:
        raise AppError("deviceId is required", "VALIDATION_ERROR", 400)
    if not pin_allowed(user):
        raise AppError(
            "PIN unlock is for Production Workers. Sign in with your password.",
            "PIN_NOT_ALLOWED",
            403,
        )
    validate_pin(pin)
    # PIN may only be set on a device that already completed a password login.
    device = UserDevice.query.filter_by(user_id=user.id, device_id=device_id).first()
    if not device or device.revoked_at is not None:
        raise AppError(
            "Complete a password login on this device before setting a PIN",
            "PIN_DEVICE_REQUIRED",
            400,
        )
    if device_label:
        device.device_label = device_label
    pin_hash = bcrypt.generate_password_hash(pin).decode("utf-8")
    now = _utcnow()
    # One PIN for the account: every signed-in phone gets the same one.
    phones = UserDevice.query.filter(
        UserDevice.user_id == user.id,
        UserDevice.revoked_at.is_(None),
    ).all()
    for phone in phones:
        phone.pin_hash = pin_hash
        phone.pin_set_at = now
        phone.pin_failed_attempts = 0
    write_audit_event(
        "PIN_SET",
        "UserDevice",
        device.id,
        after={"userId": user.id, "deviceId": device_id, "devices": len(phones)},
    )
    db.session.commit()
    return device


def remove_device_pin(user: User, device_id: str) -> None:
    """Turn the PIN off for the account, on every phone."""
    device = UserDevice.query.filter_by(user_id=user.id, device_id=device_id).first()
    if not device or not device.pin_hash:
        raise AppError("No PIN on this device", "NOT_FOUND", 404)
    phones = UserDevice.query.filter(
        UserDevice.user_id == user.id,
        UserDevice.pin_hash.isnot(None),
    ).all()
    for phone in phones:
        phone.pin_hash = None
        phone.pin_set_at = None
        phone.pin_failed_attempts = 0
    write_audit_event(
        "PIN_REMOVED", "UserDevice", device.id, after={"userId": user.id, "devices": len(phones)}
    )
    db.session.commit()


def _device_not_set_up() -> AppError:
    return AppError(
        "This device isn't set up — sign in with your password",
        "PIN_DEVICE_UNKNOWN",
        401,
    )


def unlock_with_pin(device_id: str, pin: str) -> dict:
    if not device_id or not pin:
        raise _device_not_set_up()
    if not is_pin_format(pin):
        # PINs used to be 6 digits; those stop working and the worker signs in
        # with the password and sets a new 4-digit PIN.
        raise AppError(
            f"Enter your {PIN_LENGTH}-digit PIN, or sign in with your password.",
            "VALIDATION_ERROR",
            400,
        )

    # One browser shares a device id across accounts. Match the row that
    # actually has a PIN, not whichever row the database returns first.
    candidates = (
        UserDevice.query.filter(
            UserDevice.device_id == device_id,
            UserDevice.revoked_at.is_(None),
            UserDevice.pin_hash.isnot(None),
        )
        .order_by(UserDevice.pin_set_at.desc())
        .all()
    )
    active: list[tuple[UserDevice, User]] = []
    for row in candidates:
        owner = db.session.get(User, row.user_id)
        if owner and owner.status == UserStatus.ACTIVE and pin_allowed(owner):
            active.append((row, owner))

    if not active:
        write_audit_event(
            "PIN_FAILED",
            "UserDevice",
            None,
            after={"deviceId": device_id, "reason": "unknown_or_revoked"},
        )
        db.session.commit()
        raise _device_not_set_up()

    for device, user in active:
        if bcrypt.check_password_hash(device.pin_hash, pin):
            device.pin_failed_attempts = 0
            device.last_used_at = _utcnow()
            write_audit_event(
                "LOGIN_SUCCEEDED",
                "User",
                user.id,
                after={"via": "PIN", "device": device.device_label},
                actor=user,
            )
            db.session.commit()
            return issue_tokens(user, device)

    device, owner = active[0]
    device.pin_failed_attempts = (device.pin_failed_attempts or 0) + 1
    write_audit_event(
        "PIN_FAILED",
        "UserDevice",
        device.id,
        after={
            "userId": device.user_id,
            "deviceId": device_id,
            "attempts": device.pin_failed_attempts,
        },
        actor=owner,
    )
    if device.pin_failed_attempts >= MAX_PIN_FAILURES:
        device.pin_hash = None
        device.pin_set_at = None
        device.revoked_at = _utcnow()
        write_audit_event(
            "DEVICE_REVOKED",
            "UserDevice",
            device.id,
            after={"userId": device.user_id, "reason": "pin_lockout"},
            actor=owner,
        )
        db.session.commit()
        raise AppError(
            "PIN has been removed on this device. Sign in with your password.",
            "PIN_LOCKED",
            401,
        )
    db.session.commit()
    remaining = MAX_PIN_FAILURES - device.pin_failed_attempts
    raise AppError(
        f"PIN incorrect. {remaining} attempt{'s' if remaining != 1 else ''} left.",
        "PIN_INVALID",
        401,
    )


def prune_stale_devices(user_id: str) -> int:
    cutoff = _utcnow() - timedelta(days=STALE_DEVICE_DAYS)
    stale = UserDevice.query.filter(
        UserDevice.user_id == user_id,
        UserDevice.pin_hash.is_(None),
        func.coalesce(UserDevice.last_used_at, UserDevice.created_at) < cutoff,
    ).all()
    for device in stale:
        db.session.delete(device)
    if stale:
        db.session.commit()
    return len(stale)


def list_user_devices(user_id: str) -> list[UserDevice]:
    prune_stale_devices(user_id)
    return (
        UserDevice.query.filter_by(user_id=user_id)
        .order_by(func.coalesce(UserDevice.last_used_at, UserDevice.created_at).desc())
        .all()
    )


def revoke_device(user_id: str, device_row_id: str) -> UserDevice:
    device = UserDevice.query.filter_by(id=device_row_id, user_id=user_id).first()
    if not device:
        raise AppError("Device not found", "NOT_FOUND", 404)
    device.revoked_at = _utcnow()
    device.pin_hash = None
    device.pin_set_at = None
    write_audit_event("DEVICE_REVOKED", "UserDevice", device.id, after={"userId": user_id})
    db.session.commit()
    return device


def revoke_all_devices_for_user(user_id: str, keep_device_row_id: str | None = None) -> int:
    """Sign out every device; ``keep_device_row_id`` stays signed in but loses its PIN."""
    now = _utcnow()
    devices = UserDevice.query.filter(
        UserDevice.user_id == user_id,
        UserDevice.revoked_at.is_(None),
    ).all()
    revoked = 0
    for device in devices:
        device.pin_hash = None
        device.pin_set_at = None
        device.pin_failed_attempts = 0
        if device.id != keep_device_row_id:
            device.revoked_at = now
            revoked += 1
    write_audit_event(
        "DEVICE_REVOKED",
        "User",
        user_id,
        after={"count": revoked, "scope": "all" if not keep_device_row_id else "others"},
    )
    db.session.commit()
    return revoked


def sign_out_other_devices(user_id: str, current_device_row_id: str | None) -> int:
    """Sign out every device except the current one. Other devices also lose their PIN."""
    if not current_device_row_id:
        raise AppError(
            "Sign in again on this device first",
            "DEVICE_REQUIRED",
            400,
        )
    now = _utcnow()
    others = UserDevice.query.filter(
        UserDevice.user_id == user_id,
        UserDevice.revoked_at.is_(None),
        UserDevice.id != current_device_row_id,
    ).all()
    for device in others:
        device.revoked_at = now
        device.pin_hash = None
        device.pin_set_at = None
    write_audit_event(
        "DEVICE_REVOKED",
        "User",
        user_id,
        after={"count": len(others), "scope": "others"},
    )
    db.session.commit()
    return len(others)


def device_pin_status(user_id: str, device_id: str) -> dict:
    device = UserDevice.query.filter_by(user_id=user_id, device_id=device_id).first()
    if not device or device.revoked_at is not None:
        return {"known": False, "hasPin": False}
    return {"known": True, "hasPin": bool(device.pin_hash)}
