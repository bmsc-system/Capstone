from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy.orm import joinedload

from app.middleware.rbac import require_roles
from app.models.notification import NotificationLog
from app.models.user import UserRole
from app.services import notification_service as notif_service

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.route("", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN, UserRole.OFFICE_STAFF)
def list_notifications():
    logs = notif_service.list_notification_logs(
        job_order_id=request.args.get("jobOrderId") or request.args.get("job_order_id"),
        client_id=request.args.get("clientId") or request.args.get("client_id"),
        status=request.args.get("status"),
        limit=request.args.get("limit", 200),
        event=request.args.get("event"),
    )
    # Eager relationships for to_dict
    ids = [log.id for log in logs]
    if ids:
        logs = (
            NotificationLog.query.options(
                joinedload(NotificationLog.job_order),
                joinedload(NotificationLog.client),
            )
            .filter(NotificationLog.id.in_(ids))
            .order_by(NotificationLog.created_at.desc())
            .all()
        )
    return jsonify([log.to_dict() for log in logs])
