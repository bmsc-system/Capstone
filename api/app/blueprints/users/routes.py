from flask import Blueprint, jsonify, make_response, request
from flask_jwt_extended import jwt_required

from app.extensions import db
from app.middleware.rbac import get_current_user_id, get_current_user_role, require_roles
from app.models.user import User, UserRole, UserStatus
from app.models.user_security import InvitationChannel
from app.services.auth_service import get_user_by_id, update_user
from app.services.device_pin_service import revoke_all_devices_for_user
from app.services.invitation_service import (
    create_invited_user,
    resend_invitation,
    revoke_invitation,
)
from app.services.notification_service import delivery_summary
from app.services import user_photo_service
from app.utils.errors import AppError

users_bp = Blueprint("users", __name__)


@users_bp.route("", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def list_users():
    role = request.args.get("role")
    active = request.args.get("active")
    status = request.args.get("status")
    query = User.query
    if role:
        query = query.filter_by(role=UserRole(role))
    if status:
        query = query.filter_by(status=UserStatus(status))
    elif active is not None:
        query = query.filter_by(active=active.lower() == "true")
    users = query.order_by(User.full_name).all()
    return jsonify(
        [u.to_dict(include_profile=True, include_skills=True) for u in users]
    )


@users_bp.route("", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def create_user_route():
    data = request.get_json() or {}
    required = ["email", "fullName", "role"]
    for field in required:
        if not data.get(field):
            return (
                jsonify(
                    {
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": f"{field} is required",
                        }
                    }
                ),
                400,
            )
    if data.get("password"):
        return (
            jsonify(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Passwords are set by the user via invitation",
                    }
                }
            ),
            400,
        )

    channel_raw = (data.get("inviteChannel") or data.get("channel") or "EMAIL").upper()
    if channel_raw != "EMAIL":
        raise AppError("Invitations are sent by email only", "VALIDATION_ERROR", 400)

    user, invitation, _raw = create_invited_user(
        full_name=data["fullName"],
        email=data.get("email") or "",
        mobile_number=data.get("mobileNumber"),
        role=UserRole(data["role"]),
        channel=InvitationChannel.EMAIL,
        created_by_id=get_current_user_id(),
        nickname=data.get("nickname"),
    )
    payload = user.to_dict(include_profile=True, include_skills=True)
    payload["invitation"] = invitation.to_dict()
    payload["delivery"] = delivery_summary(getattr(invitation, "delivery", None))
    # One-time setup link for the admin (email may be delayed or console-only).
    payload["setupUrl"] = getattr(invitation, "setup_url", None)
    return jsonify(payload), 201


@users_bp.route("/<user_id>", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def get_user(user_id):
    user = get_user_by_id(user_id)
    return jsonify(
        user.to_dict(include_profile=True, include_skills=True, include_schedule=True)
    )


@users_bp.route("/<user_id>", methods=["PATCH"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def update_user_route(user_id):
    user = get_user_by_id(user_id)
    data = request.get_json() or {}
    payload = {}
    if "email" in data:
        payload["email"] = data["email"]
    if "mobileNumber" in data:
        payload["mobileNumber"] = data["mobileNumber"]
    if "fullName" in data:
        payload["fullName"] = data["fullName"]
    if "nickname" in data:
        payload["nickname"] = data["nickname"]
    if "role" in data:
        payload["role"] = UserRole(data["role"])
    if "status" in data:
        payload["status"] = data["status"]
    if "active" in data:
        payload["active"] = data["active"]
    # Admin must not set passwords directly.
    if "password" in data and data["password"]:
        raise AppError(
            "Admins cannot set user passwords; use invitation or ask the user to change it",
            "VALIDATION_ERROR",
            400,
        )

    old_email = user.email
    user = update_user(user, payload)
    body = user.to_dict(include_profile=True, include_skills=True)
    # The old setup link was revoked with the email change; a user still
    # waiting to set a password gets a fresh link at the corrected address.
    if user.email != old_email and user.status == UserStatus.INVITED:
        invitation, _raw = resend_invitation(user, get_current_user_id(), InvitationChannel.EMAIL)
        body["delivery"] = delivery_summary(getattr(invitation, "delivery", None))
        body["setupUrl"] = getattr(invitation, "setup_url", None)
    return jsonify(body)


@users_bp.route("/<user_id>", methods=["DELETE"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def deactivate_user(user_id):
    user = get_user_by_id(user_id)
    user.status = UserStatus.DISABLED
    user.sync_active_flag()
    db.session.commit()
    revoke_all_devices_for_user(user.id)
    return jsonify({"message": "User disabled"})


@users_bp.route("/<user_id>/invite", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def resend_invite(user_id):
    user = get_user_by_id(user_id)
    data = request.get_json() or {}
    channel_raw = (data.get("channel") or data.get("inviteChannel") or "EMAIL").upper()
    if channel_raw != "EMAIL":
        raise AppError("Invitations are sent by email only", "VALIDATION_ERROR", 400)
    # deliver=false: the copy-link fallback, for when the email does not arrive.
    deliver = data.get("deliver", True) is not False
    invitation, _raw = resend_invitation(
        user, get_current_user_id(), InvitationChannel.EMAIL, deliver=deliver
    )
    payload = invitation.to_dict()
    payload["delivery"] = (
        delivery_summary(getattr(invitation, "delivery", None)) if deliver else None
    )
    payload["setupUrl"] = getattr(invitation, "setup_url", None)
    return jsonify(payload)


@users_bp.route("/<user_id>/invite", methods=["DELETE"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def revoke_invite(user_id):
    user = get_user_by_id(user_id)
    count = revoke_invitation(user)
    return jsonify({"revoked": count})


@users_bp.route("/<user_id>/photo", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def upload_photo(user_id):
    if (request.content_length or 0) > user_photo_service.MAX_PHOTO_BYTES + 64 * 1024:
        raise AppError("The photo must be 2 MB or smaller", "PHOTO_TOO_LARGE", 413)
    upload = request.files.get("file")
    if upload is None:
        raise AppError("Choose a photo to upload", "VALIDATION_ERROR", 400)
    raw = upload.stream.read(user_photo_service.MAX_PHOTO_BYTES + 1)
    user = user_photo_service.save_photo(user_id, raw)
    return jsonify(user.to_dict(include_profile=True, include_skills=True))


@users_bp.route("/<user_id>/photo", methods=["DELETE"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def remove_photo(user_id):
    user = user_photo_service.delete_photo(user_id)
    return jsonify(user.to_dict(include_profile=True, include_skills=True))


@users_bp.route("/<user_id>/photo", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN, UserRole.OFFICE_STAFF, UserRole.PRODUCTION_WORKER)
def get_photo(user_id):
    row = user_photo_service.get_photo(get_current_user_id(), get_current_user_role(), user_id)
    resp = make_response(row.data)
    resp.headers["Content-Type"] = "image/jpeg"
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@users_bp.route("/<user_id>/devices", methods=["DELETE"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def revoke_user_devices(user_id):
    get_user_by_id(user_id)
    count = revoke_all_devices_for_user(user_id)
    return jsonify({"revoked": count})
