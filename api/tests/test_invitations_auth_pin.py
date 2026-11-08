"""Invitation, identifier login, and device PIN tests.

Runs against the test database (TEST_DATABASE_URL) inside a session that always
rolls back.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.config import Config
from app.extensions import bcrypt, db
from app.models.user import User, UserRole, UserStatus
from app.models.user_security import InvitationChannel, UserDevice, UserInvitation
from app.services.device_pin_service import (
    register_device_after_password_login,
    set_device_pin,
)
from app.services.invitation_service import create_invited_user, issue_invitation
from app.utils.errors import AppError
from app.utils.phone import normalize_ph_mobile


class LocalTxnConfig(Config):
    """Same local Postgres as development; tests never commit."""

    TESTING = True
    ENV = "production"
    DEBUG = False
    RATELIMIT_ENABLED = False
    RATELIMIT_STORAGE_URI = "memory://"


@pytest.fixture
def app():
    return create_app(LocalTxnConfig)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _rollback_txn(app, monkeypatch):
    """Never commit — flush only, then roll back.

    Keep a single app context for the whole test. Nested app_context() blocks
    tear down the session on exit and would lose flushed rows.
    """
    with app.app_context():
        monkeypatch.setattr(db.session, "commit", db.session.flush)
        try:
            yield
        finally:
            db.session.rollback()
            db.session.remove()


def _admin():
    admin = User(
        email="invite_admin@test.local",
        mobile_number="+639170000001",
        password_hash=bcrypt.generate_password_hash("Admin123!").decode("utf-8"),
        full_name="Admin",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(admin)
    db.session.flush()
    return admin


def _active_user(email, mobile, password="Worker123!"):
    user = User(
        email=email,
        mobile_number=mobile,
        password_hash=bcrypt.generate_password_hash(password).decode("utf-8"),
        full_name=email.split("@")[0],
        role=UserRole.PRODUCTION_WORKER,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _set_pin(user, device_id, pin):
    register_device_after_password_login(user, device_id, "Test phone")
    return set_device_pin(user, device_id, pin, "Test phone")


def test_invited_user_cannot_login(client):
    db.session.add(
        User(
            email="invited@test.local",
            mobile_number="+639170000010",
            password_hash=None,
            full_name="Invited User",
            role=UserRole.OFFICE_STAFF,
            status=UserStatus.INVITED,
            active=False,
        )
    )
    db.session.flush()

    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": "invited@test.local", "password": "Whatever1!"},
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_expired_and_used_invitation_rejected(client):
    admin = _admin()
    user, invitation, raw = create_invited_user(
        full_name="Temp Worker",
        email="temp@test.local",
        mobile_number="09171234567",
        role=UserRole.PRODUCTION_WORKER,
        channel=InvitationChannel.EMAIL,
        created_by_id=admin.id,
    )
    invitation.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.session.flush()
    expired_token = raw

    resp = client.post(
        "/api/v1/auth/invitation/validate",
        json={"token": expired_token},
    )
    assert resp.status_code == 400

    _inv, raw2 = issue_invitation(
        user, InvitationChannel.EMAIL, admin.id, commit=True
    )

    ok = client.post(
        "/api/v1/auth/invitation/accept",
        json={
            "token": raw2,
            "password": "SecurePass1!",
            "passwordConfirm": "SecurePass1!",
        },
    )
    assert ok.status_code == 200
    assert "accessToken" in ok.get_json()

    reused = client.post(
        "/api/v1/auth/invitation/accept",
        json={
            "token": raw2,
            "password": "SecurePass1!",
            "passwordConfirm": "SecurePass1!",
        },
    )
    assert reused.status_code == 400


def test_weak_password_leaves_invitation_open_for_retry(client):
    admin = _admin()
    _user, invitation, raw = create_invited_user(
        full_name="Weak Pass User",
        email="weakpass@test.local",
        mobile_number="09170002222",
        role=UserRole.OFFICE_STAFF,
        channel=InvitationChannel.EMAIL,
        created_by_id=admin.id,
    )
    invitation_id = invitation.id

    rejected = client.post(
        "/api/v1/auth/invitation/accept",
        json={
            "token": raw,
            "password": "Password123",
            "passwordConfirm": "Password123",
        },
    )
    assert rejected.status_code == 400
    message = rejected.get_json()["error"]["message"]
    assert "8 characters" in message
    assert "common password" in message.lower()

    still_open = db.session.get(UserInvitation, invitation_id)
    assert still_open is not None
    assert still_open.used_at is None
    assert still_open.revoked_at is None
    assert still_open.is_active

    ok = client.post(
        "/api/v1/auth/invitation/accept",
        json={
            "token": raw,
            "password": "SecurePass1!",
            "passwordConfirm": "SecurePass1!",
        },
    )
    assert ok.status_code == 200
    assert "accessToken" in ok.get_json()

    used = db.session.get(UserInvitation, invitation_id)
    assert used.used_at is not None


def test_login_with_email_or_mobile_formats(client):
    db.session.add(
        User(
            email="mobileuser@test.local",
            mobile_number=normalize_ph_mobile("09171234567"),
            password_hash=bcrypt.generate_password_hash("Worker123!").decode("utf-8"),
            full_name="Mobile User",
            role=UserRole.PRODUCTION_WORKER,
            status=UserStatus.ACTIVE,
            active=True,
        )
    )
    db.session.flush()

    for identifier in (
        "mobileuser@test.local",
        "09171234567",
        "639171234567",
        "+63 917 123 4567",
        "0917-123-4567",
    ):
        resp = client.post(
            "/api/v1/auth/login",
            json={"identifier": identifier, "password": "Worker123!"},
        )
        assert resp.status_code == 200, identifier
        assert "accessToken" in resp.get_json()


def test_duplicate_normalized_mobile_rejected(client):
    admin = _admin()
    create_invited_user(
        full_name="First",
        email="first@test.local",
        mobile_number="09170001111",
        role=UserRole.OFFICE_STAFF,
        channel=InvitationChannel.EMAIL,
        created_by_id=admin.id,
    )
    db.session.flush()

    login = client.post(
        "/api/v1/auth/login",
        json={"identifier": "invite_admin@test.local", "password": "Admin123!"},
    )
    assert login.status_code == 200
    token = login.get_json()["accessToken"]
    resp = client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "fullName": "Second",
            "email": "second@test.local",
            "mobileNumber": "+639170001111",
            "role": "OFFICE_STAFF",
            "inviteChannel": "EMAIL",
        },
    )
    assert resp.status_code == 409


def test_pin_unlock_rejects_unknown_or_revoked_device(client):
    user = _active_user("pinuser@test.local", "+639170000099")

    unknown = client.post(
        "/api/v1/auth/pin/unlock",
        json={"deviceId": "00000000-0000-0000-0000-000000000001", "pin": "2583"},
    )
    assert unknown.status_code == 401
    assert unknown.get_json()["error"]["code"] == "PIN_DEVICE_UNKNOWN"
    assert "isn't set up" in unknown.get_json()["error"]["message"]

    _set_pin(user, "dev-known-1", "2583")
    device = UserDevice.query.filter_by(device_id="dev-known-1").first()
    device.revoked_at = datetime.now(timezone.utc)
    db.session.flush()

    revoked = client.post(
        "/api/v1/auth/pin/unlock",
        json={"deviceId": "dev-known-1", "pin": "2583"},
    )
    assert revoked.status_code == 401
    assert revoked.get_json()["error"]["code"] == "PIN_DEVICE_UNKNOWN"


def test_pin_unlock_uses_the_row_that_has_a_pin(client):
    """Same browser device id is stored once per user. Unlock must not pick a pin-less row."""
    admin = _active_user("pinadmin@test.local", "+639170000091")
    admin.role = UserRole.ADMIN
    worker = _active_user("pinworker@test.local", "+639170000092")
    shared = "shared-browser-device"
    register_device_after_password_login(admin, shared, "Office PC")
    _set_pin(worker, shared, "2583")

    wrong = client.post(
        "/api/v1/auth/pin/unlock",
        json={"deviceId": shared, "pin": "0000"},
    )
    assert wrong.status_code == 401
    assert wrong.get_json()["error"]["code"] == "PIN_INVALID"
    assert wrong.get_json()["error"]["message"].startswith("PIN incorrect")

    ok = client.post(
        "/api/v1/auth/pin/unlock",
        json={"deviceId": shared, "pin": "2583"},
    )
    assert ok.status_code == 200
    assert "accessToken" in ok.get_json()


def test_set_pin_then_unlock_without_a_session(client):
    user = _active_user("pinflow@test.local", "+639170000093")
    login = client.post(
        "/api/v1/auth/login",
        json={
            "identifier": "pinflow@test.local",
            "password": "Worker123!",
            "deviceId": "phone-after-logout",
        },
    )
    assert login.status_code == 200
    token = login.get_json()["accessToken"]

    saved = client.post(
        "/api/v1/auth/pin",
        headers={"Authorization": f"Bearer {token}"},
        json={"deviceId": "phone-after-logout", "pin": "2583"},
    )
    assert saved.status_code == 201

    # Logout only drops the client token. The device binding stays.
    unlocked = client.post(
        "/api/v1/auth/pin/unlock",
        json={"deviceId": "phone-after-logout", "pin": "2583"},
    )
    assert unlocked.status_code == 200
    assert unlocked.get_json()["user"]["id"] == user.id


def test_five_failed_pin_attempts_revokes_pin(client):
    user = _active_user("pinlock@test.local", "+639170000088")
    _set_pin(user, "dev-lock", "2583")

    for _ in range(5):
        assert (
            client.post(
                "/api/v1/auth/pin/unlock",
                json={"deviceId": "dev-lock", "pin": "0000"},
            ).status_code
            == 401
        )

    device = UserDevice.query.filter_by(device_id="dev-lock").first()
    assert device is not None
    assert device.pin_hash is None
    assert device.revoked_at is not None


def test_pin_is_four_digits(client):
    user = _active_user("pin4@test.local", "+639170000087")
    register_device_after_password_login(user, "dev-four", "Test phone")
    for bad in ("482917", "482", "12a4"):
        with pytest.raises(AppError) as err:
            set_device_pin(user, "dev-four", bad, "Test phone")
        assert "4 digits" in err.value.message
    for obvious in ("1111", "1234", "4321", "1212"):
        with pytest.raises(AppError) as err:
            set_device_pin(user, "dev-four", obvious, "Test phone")
        assert "less obvious" in err.value.message
    set_device_pin(user, "dev-four", "4829", "Test phone")
    ok = client.post("/api/v1/auth/pin/unlock", json={"deviceId": "dev-four", "pin": "4829"})
    assert ok.status_code == 200


def test_old_six_digit_pin_no_longer_unlocks(client):
    user = _active_user("pin6@test.local", "+639170000086")
    register_device_after_password_login(user, "dev-six", "Test phone")
    device = UserDevice.query.filter_by(device_id="dev-six").first()
    device.pin_hash = bcrypt.generate_password_hash("482917").decode()
    device.pin_set_at = datetime.now(timezone.utc)
    db.session.commit()
    res = client.post("/api/v1/auth/pin/unlock", json={"deviceId": "dev-six", "pin": "482917"})
    assert res.status_code == 400
    # A rejected format is not a wrong guess: the device keeps its attempts.
    db.session.refresh(device)
    assert (device.pin_failed_attempts or 0) == 0


def test_password_change_revokes_all_device_pins(client):
    user = _active_user("pch@test.local", "+639170000077")
    _set_pin(user, "dev-a", "2583")
    _set_pin(user, "dev-b", "1472")
    user_id = user.id

    login = client.post(
        "/api/v1/auth/login",
        json={"identifier": "pch@test.local", "password": "Worker123!"},
    )
    assert login.status_code == 200
    token = login.get_json()["accessToken"]
    resp = client.post(
        "/api/v1/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": "Worker123!", "newPassword": "NewPass99!"},
    )
    assert resp.status_code == 200

    devices = UserDevice.query.filter_by(user_id=user_id).all()
    assert all(d.revoked_at is not None for d in devices)
    assert all(d.pin_hash is None for d in devices)


def test_failed_login_does_not_reveal_identifier(client):
    missing = client.post(
        "/api/v1/auth/login",
        json={"identifier": "nobody@test.local", "password": "WrongPass1!"},
    )
    db.session.add(
        User(
            email="exists@test.local",
            mobile_number="+639170000066",
            password_hash=bcrypt.generate_password_hash("RightPass1!").decode("utf-8"),
            full_name="Exists",
            role=UserRole.OFFICE_STAFF,
            status=UserStatus.ACTIVE,
            active=True,
        )
    )
    db.session.flush()

    wrong = client.post(
        "/api/v1/auth/login",
        json={"identifier": "exists@test.local", "password": "WrongPass1!"},
    )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.get_json()["error"]["message"] == wrong.get_json()["error"]["message"]
    assert missing.get_json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_normalize_ph_mobile_variants():
    assert normalize_ph_mobile("09171234567") == "+639171234567"
    assert normalize_ph_mobile("639171234567") == "+639171234567"
    assert normalize_ph_mobile("+63 917-123-4567") == "+639171234567"


def _invited_worker(admin, mobile="09175550111", email="worker@test.local"):
    """A worker with a copied setup link (the fallback when the email does not arrive)."""
    user = User(
        email=email,
        mobile_number=normalize_ph_mobile(mobile),
        password_hash=None,
        full_name="Copied Link Worker",
        role=UserRole.PRODUCTION_WORKER,
        status=UserStatus.INVITED,
        active=False,
    )
    db.session.add(user)
    db.session.flush()
    invitation, raw = issue_invitation(
        user, InvitationChannel.EMAIL, admin.id, commit=True, deliver=False
    )
    assert invitation.setup_url
    assert getattr(invitation, "delivery", None) is None
    assert user.status == UserStatus.INVITED
    assert user.active is False
    return user, invitation, raw


def test_setup_link_needs_an_email(client):
    admin = _admin()
    user = User(
        email=None,
        mobile_number=normalize_ph_mobile("09175550100"),
        full_name="No Email Worker",
        role=UserRole.PRODUCTION_WORKER,
        status=UserStatus.INVITED,
        active=False,
    )
    db.session.add(user)
    db.session.flush()
    with pytest.raises(AppError) as err:
        issue_invitation(user, InvitationChannel.EMAIL, admin.id, commit=True)
    assert "email" in err.value.message.lower()


def test_copied_setup_link_then_login_by_mobile(client):
    admin = _admin()
    user, _invitation, raw = _invited_worker(admin)

    accepted = client.post(
        "/api/v1/auth/invitation/accept",
        json={
            "token": raw,
            "password": "SecurePass1!",
            "passwordConfirm": "SecurePass1!",
        },
    )
    assert accepted.status_code == 200
    db.session.refresh(user)
    assert user.status == UserStatus.ACTIVE

    login = client.post(
        "/api/v1/auth/login",
        json={"identifier": "09175550111", "password": "SecurePass1!"},
    )
    assert login.status_code == 200
    assert "accessToken" in login.get_json()


def test_setup_link_is_single_use_and_expires(client):
    admin = _admin()
    user, invitation, raw = _invited_worker(admin, "09175550122", "w122@test.local")
    invitation.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.session.flush()

    expired = client.post("/api/v1/auth/invitation/validate", json={"token": raw})
    assert expired.status_code == 400

    _fresh, raw2 = issue_invitation(user, InvitationChannel.EMAIL, admin.id, commit=True, deliver=False)
    ok = client.post(
        "/api/v1/auth/invitation/accept",
        json={
            "token": raw2,
            "password": "SecurePass1!",
            "passwordConfirm": "SecurePass1!",
        },
    )
    assert ok.status_code == 200
    reused = client.post(
        "/api/v1/auth/invitation/accept",
        json={
            "token": raw2,
            "password": "SecurePass1!",
            "passwordConfirm": "SecurePass1!",
        },
    )
    assert reused.status_code == 400


def test_regenerate_setup_link_revokes_the_previous_one(client):
    admin = _admin()
    user, _invitation, first = _invited_worker(admin, "09175550133", "w133@test.local")
    _second, second = issue_invitation(
        user, InvitationChannel.EMAIL, admin.id, commit=True, deliver=False
    )

    old = client.post("/api/v1/auth/invitation/validate", json={"token": first})
    assert old.status_code == 400

    current = client.post("/api/v1/auth/invitation/validate", json={"token": second})
    assert current.status_code == 200
    assert current.get_json()["email"] == "w133@test.local"


def test_pin_unlock_is_for_production_workers_only(client, app):
    """Admin and Office Staff sign in with a password; they cannot set a PIN,
    and an old PIN on their device stops working."""
    from app.extensions import bcrypt as _bcrypt, db as _db
    from app.models.user import User, UserRole, UserStatus
    from app.models.user_security import UserDevice

    office = User(
        email="pin-office@test.local",
        full_name="Pin Office",
        password_hash=_bcrypt.generate_password_hash("Office-pass-2031").decode(),
        role=UserRole.OFFICE_STAFF,
        status=UserStatus.ACTIVE,
    )
    _db.session.add(office)
    _db.session.commit()
    res = client.post(
        "/api/v1/auth/login",
        json={"identifier": "pin-office@test.local", "password": "Office-pass-2031", "deviceId": "dev-office-1"},
    )
    assert res.status_code == 200, res.get_json()
    token = res.get_json()["accessToken"]
    res = client.post(
        "/api/v1/auth/pin",
        json={"deviceId": "dev-office-1", "pin": "4816"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == "PIN_NOT_ALLOWED"

    # A PIN left from before still does not unlock.
    device = UserDevice.query.filter_by(user_id=office.id, device_id="dev-office-1").first()
    device.pin_hash = _bcrypt.generate_password_hash("4816").decode()
    _db.session.commit()
    res = client.post("/api/v1/auth/pin/unlock", json={"deviceId": "dev-office-1", "pin": "4816"})
    assert res.status_code == 401


def test_role_change_or_disable_signs_the_user_out(client):
    from app.services.auth_service import update_user

    _admin()
    for i, payload in enumerate(({"role": UserRole.OFFICE_STAFF}, {"status": "DISABLED"})):
        user = _active_user(f"signout{i}@test.local", f"+63917000009{i}")
        register_device_after_password_login(user, f"dev-{i}", "Test phone")
        update_user(user, payload)
        devices = UserDevice.query.filter_by(user_id=user.id).all()
        assert devices and all(d.revoked_at is not None for d in devices), payload


def test_disable_or_role_change_ends_tokens_without_a_device(client):
    """A token from a login that sent no device id still stops at once."""
    from app.services.auth_service import update_user

    _admin()
    for i, payload in enumerate(({"status": "DISABLED"}, {"role": UserRole.OFFICE_STAFF})):
        _active_user(f"nodev{i}@test.local", f"+63917000008{i}")
        login = client.post(
            "/api/v1/auth/login",
            json={"identifier": f"nodev{i}@test.local", "password": "Worker123!"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.get_json()['accessToken']}"}
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
        update_user(User.query.filter_by(email=f"nodev{i}@test.local").one(), payload)
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401, payload


def test_oversized_request_is_refused(client):
    big = b"x" * (5 * 1024 * 1024)
    res = client.post(
        "/api/v1/auth/login", data=big, headers={"Content-Type": "application/json"}
    )
    assert res.status_code == 413
    assert res.get_json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_changing_a_pending_users_email_revokes_the_old_link(client):
    """A link sent to a mistyped address must stop working once the email is fixed."""
    from flask_jwt_extended import create_access_token

    admin = _admin()
    user, _inv, old_raw = create_invited_user(
        full_name="Pending Office",
        email="pending.typo@test.local",
        mobile_number="",
        role=UserRole.OFFICE_STAFF,
        created_by_id=admin.id,
    )
    token = create_access_token(identity=admin.id, additional_claims={"role": "ADMIN"})
    res = client.patch(
        f"/api/v1/users/{user.id}",
        json={"email": "pending.right@test.local"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["email"] == "pending.right@test.local"
    assert body["delivery"] is not None and body["setupUrl"]

    accept = {"password": "Pending12345!x", "passwordConfirm": "Pending12345!x"}
    old = client.post("/api/v1/auth/invitation/accept", json={"token": old_raw, **accept})
    assert old.status_code == 400
    assert old.get_json()["error"]["code"] == "INVITE_REVOKED"

    new_raw = body["setupUrl"].split("token=", 1)[1]
    new = client.post("/api/v1/auth/invitation/accept", json={"token": new_raw, **accept})
    assert new.status_code == 200, new.get_json()


def test_editing_other_details_keeps_the_setup_link(client):
    from app.services.auth_service import update_user

    admin = _admin()
    user, _inv, raw = create_invited_user(
        full_name="Pending Clerk",
        email="pending.clerk@test.local",
        mobile_number="",
        role=UserRole.OFFICE_STAFF,
        created_by_id=admin.id,
    )
    update_user(user, {"fullName": "Pending Clerk Two", "email": "pending.clerk@test.local"})
    res = client.post(
        "/api/v1/auth/invitation/accept",
        json={"token": raw, "password": "Pending12345!x", "passwordConfirm": "Pending12345!x"},
    )
    assert res.status_code == 200, res.get_json()
