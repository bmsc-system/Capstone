from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.middleware.rbac import get_current_user_id, require_roles
from app.models.user import UserRole
from app.services import staff_alert_service as svc

alerts_bp = Blueprint("alerts", __name__)

_STAFF = (UserRole.ADMIN, UserRole.OFFICE_STAFF)


@alerts_bp.route("", methods=["GET"])
@jwt_required()
@require_roles(*_STAFF)
def list_alerts():
    return jsonify(
        svc.list_alerts(
            get_current_user_id(),
            limit=request.args.get("limit", type=int) or svc.LIST_LIMIT,
            offset=request.args.get("offset", type=int) or 0,
            unread_only=request.args.get("unread") in ("1", "true"),
        )
    )


@alerts_bp.route("/unread-count", methods=["GET"])
@jwt_required()
@require_roles(*_STAFF)
def unread_count():
    return jsonify({"unreadCount": svc.unread_count(get_current_user_id())})


@alerts_bp.route("/<alert_id>/read", methods=["POST"])
@jwt_required()
@require_roles(*_STAFF)
def mark_read(alert_id):
    return jsonify(svc.mark_read(get_current_user_id(), alert_id))


@alerts_bp.route("/read-all", methods=["POST"])
@jwt_required()
@require_roles(*_STAFF)
def mark_all_read():
    return jsonify({"marked": svc.mark_all_read(get_current_user_id())})
