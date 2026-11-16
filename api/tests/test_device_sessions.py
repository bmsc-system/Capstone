"""Device-bound sessions: sign-out, sign out others, refresh upgrade, stale cleanup.

Runs against the test database (TEST_DATABASE_URL) inside a session that always rolls back.
"""

from datetime import datetime, timedelta, timezone

import pytest
from flask_jwt_extended import create_refresh_token, decode_token

from app import create_app
from app.config import Config
from app.extensions import bcrypt, db
from app.models.user import User, UserRole, UserStatus
from app.models.user_security import UserDevice
from app.services.device_pin_service import STALE_DEVICE_DAYS, set_device_pin

PASSWORD = "Worker123!"


class LocalTxnConfig(Config):
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
    with app.app_context():
        monkeypatch.setattr(db.session, "commit", db.session.flush)
        try:
            yield
        finally:
            db.session.rollback()
            db.session.remove()


def _user(email):
    user = User(
        email=email,
        password_hash=bcrypt.generate_password_hash(PASSWORD).decode("utf-8"),
        full_name=email.split("@")[0],
        role=UserRole.PRODUCTION_WORKER,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _login(client, email, device_id, label="Android phone · Chrome 141"):
    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": email, "password": PASSWORD, "deviceId": device_id, "deviceLabel": label},
    )
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _refresh(client, session, device_id=None):
    body = {"deviceId": device_id} if device_id else None
    return client.post("/api/v1/auth/refresh", headers=_auth(session["refreshToken"]), json=body)


def _me(client, session):
    return client.get("/api/v1/auth/me", headers=_auth(session["accessToken"]))


def _row(user, device_id):
    return UserDevice.query.filter_by(user_id=user.id, device_id=device_id).first()


def test_tokens_carry_the_device_row(client):
    user = _user("sess_claim@test.local")
    session = _login(client, user.email, "dev-claim")
    row = _row(user, "dev-claim")
    assert decode_token(session["accessToken"])["did"] == row.id
    assert decode_token(session["refreshToken"])["did"] == row.id


def test_refresh_updates_last_active(client):
    user = _user("sess_touch@test.local")
    session = _login(client, user.email, "dev-touch")
    row = _row(user, "dev-touch")
    row.last_used_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.session.flush()

    resp = _refresh(client, session)
    assert resp.status_code == 200
    assert "refreshToken" not in resp.get_json()
    assert row.last_used_at > datetime.now(timezone.utc) - timedelta(minutes=1)


def test_signed_out_device_cannot_refresh_or_call_api(client):
    user = _user("sess_revoke@test.local")
    phone = _login(client, user.email, "dev-phone")
    laptop = _login(client, user.email, "dev-laptop")
    phone_row = _row(user, "dev-phone")

    resp = client.delete(f"/api/v1/auth/devices/{phone_row.id}", headers=_auth(laptop["accessToken"]))
    assert resp.status_code == 200

    refreshed = _refresh(client, phone)
    assert refreshed.status_code == 401
    assert refreshed.get_json()["error"]["code"] == "DEVICE_SIGNED_OUT"
    me = _me(client, phone)
    assert me.status_code == 401
    assert me.get_json()["error"]["code"] == "DEVICE_SIGNED_OUT"

    # The other device is unaffected.
    assert _me(client, laptop).status_code == 200
    assert _refresh(client, laptop).status_code == 200


def test_sign_out_others_keeps_only_the_current_device(client):
    user = _user("sess_others@test.local")
    current = _login(client, user.email, "dev-a")
    other_b = _login(client, user.email, "dev-b")
    other_c = _login(client, user.email, "dev-c")
    set_device_pin(user, "dev-b", "2583")

    resp = client.post("/api/v1/auth/devices/sign-out-others", headers=_auth(current["accessToken"]))
    assert resp.status_code == 200
    assert resp.get_json() == {"signedOut": 2}

    active = UserDevice.query.filter_by(user_id=user.id, revoked_at=None).all()
    assert [d.device_id for d in active] == ["dev-a"]
    assert _row(user, "dev-b").pin_hash is None
    for other in (other_b, other_c):
        assert _me(client, other).status_code == 401
        assert _refresh(client, other).status_code == 401
    assert _me(client, current).status_code == 200
    assert _refresh(client, current).status_code == 200


def test_sign_in_again_after_sign_out_gets_a_new_row(client):
    user = _user("sess_again@test.local")
    old = _login(client, user.email, "dev-again")
    old_row_id = _row(user, "dev-again").id
    keeper = _login(client, user.email, "dev-keeper")
    client.delete(f"/api/v1/auth/devices/{old_row_id}", headers=_auth(keeper["accessToken"]))

    new = _login(client, user.email, "dev-again")
    new_row = _row(user, "dev-again")
    assert new_row.id != old_row_id
    assert new_row.revoked_at is None
    assert _me(client, new).status_code == 200
    # Tokens from before the sign-out stay rejected.
    assert _refresh(client, old).status_code == 401


def test_old_session_without_device_upgrades_on_refresh(client):
    user = _user("sess_legacy@test.local")
    _login(client, user.email, "dev-legacy")
    legacy = {"refreshToken": create_refresh_token(identity=user.id, additional_claims={"role": user.role.value})}

    resp = _refresh(client, legacy, device_id="dev-legacy")
    assert resp.status_code == 200
    body = resp.get_json()
    row = _row(user, "dev-legacy")
    assert decode_token(body["accessToken"])["did"] == row.id
    assert decode_token(body["refreshToken"])["did"] == row.id


def test_old_session_without_a_known_device_must_sign_in_again(client):
    user = _user("sess_legacy2@test.local")
    legacy = {"refreshToken": create_refresh_token(identity=user.id, additional_claims={"role": user.role.value})}

    no_device = _refresh(client, legacy)
    assert no_device.status_code == 401
    assert no_device.get_json()["error"]["code"] == "SESSION_EXPIRED"
    unknown = _refresh(client, legacy, device_id="never-signed-in-here")
    assert unknown.status_code == 401
    assert unknown.get_json()["error"]["code"] == "SESSION_EXPIRED"


def test_password_change_clears_every_pin_and_keeps_this_device(client):
    user = _user("sess_pw@test.local")
    current = _login(client, user.email, "dev-pw-a")
    other = _login(client, user.email, "dev-pw-b")
    set_device_pin(user, "dev-pw-a", "2583")
    set_device_pin(user, "dev-pw-b", "1472")

    resp = client.post(
        "/api/v1/auth/password",
        headers=_auth(current["accessToken"]),
        json={"currentPassword": PASSWORD, "newPassword": "NewPass99!"},
    )
    assert resp.status_code == 200

    rows = UserDevice.query.filter_by(user_id=user.id).all()
    assert all(d.pin_hash is None for d in rows)
    assert _row(user, "dev-pw-a").revoked_at is None
    assert _row(user, "dev-pw-b").revoked_at is not None
    assert _me(client, current).status_code == 200
    assert _me(client, other).status_code == 401


def test_pin_unlock_issues_a_device_bound_session(client):
    user = _user("sess_pin@test.local")
    _login(client, user.email, "dev-pin")
    set_device_pin(user, "dev-pin", "2583")
    resp = client.post("/api/v1/auth/pin/unlock", json={"deviceId": "dev-pin", "pin": "2583"})
    assert resp.status_code == 200
    assert decode_token(resp.get_json()["accessToken"])["did"] == _row(user, "dev-pin").id


def test_stale_cleanup_removes_only_inactive_devices_without_pin(client):
    user = _user("sess_stale@test.local")
    for device_id in ("dev-old", "dev-recent", "dev-old-revoked"):
        _login(client, user.email, device_id)
    long_ago = datetime.now(timezone.utc) - timedelta(days=STALE_DEVICE_DAYS + 1)
    for device_id in ("dev-old", "dev-old-revoked"):
        row = _row(user, device_id)
        row.last_used_at = long_ago
        row.created_at = long_ago
    _row(user, "dev-old-revoked").revoked_at = long_ago
    _row(user, "dev-recent").last_used_at = datetime.now(timezone.utc) - timedelta(days=STALE_DEVICE_DAYS - 1)
    db.session.flush()

    current = _login(client, user.email, "dev-now")
    listed = client.get("/api/v1/auth/devices", headers=_auth(current["accessToken"])).get_json()
    assert sorted(d["deviceId"] for d in listed) == ["dev-now", "dev-recent"]


def test_stale_cleanup_keeps_an_old_phone_that_has_the_pin(client):
    """A worker back from a long leave can still open his phone with the PIN."""
    user = _user("sess_stale_pin@test.local")
    _login(client, user.email, "dev-old-pin")
    set_device_pin(user, "dev-old-pin", "2583")
    long_ago = datetime.now(timezone.utc) - timedelta(days=STALE_DEVICE_DAYS + 1)
    row = _row(user, "dev-old-pin")
    row.last_used_at = long_ago
    row.created_at = long_ago
    db.session.flush()

    current = _login(client, user.email, "dev-now")
    listed = client.get("/api/v1/auth/devices", headers=_auth(current["accessToken"])).get_json()
    assert sorted(d["deviceId"] for d in listed) == ["dev-now", "dev-old-pin"]
