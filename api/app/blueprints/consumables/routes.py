"""Consumables: purchases recorded and hand-outs to workers by the Office Staff
(read by the Admin too); workers see what is available and what they were given."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.middleware.rbac import get_current_user_id, require_roles
from app.models.user import UserRole
from app.services import consumable_purchase_service as purchases
from app.services import consumable_issue_service as issues
from app.services import consumable_request_service as requests_svc

consumables_bp = Blueprint("consumables", __name__)


@consumables_bp.route("/purchases", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN, UserRole.OFFICE_STAFF)
def list_purchases():
    tool_id = request.args.get("toolId")
    return jsonify(purchases.purchase_rows([tool_id] if tool_id else None))


@consumables_bp.route("/purchases", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def record_purchase():
    purchase = purchases.record_purchase(request.get_json() or {}, get_current_user_id())
    return jsonify(purchase.to_dict()), 201


@consumables_bp.route("/purchases/<purchase_id>", methods=["DELETE"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def void_purchase(purchase_id):
    purchases.void_purchase(purchase_id, get_current_user_id())
    return jsonify({"removed": True})


@consumables_bp.route("/for-worker", methods=["GET"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def worker_list():
    """What is available and what was handed to this worker. No prices."""
    return jsonify(issues.worker_view(get_current_user_id()))


@consumables_bp.route("/issues", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN, UserRole.OFFICE_STAFF)
def list_issues():
    return jsonify(
        issues.list_issues(
            tool_id=request.args.get("toolId"),
            worker_id=request.args.get("workerId"),
            job_order_id=request.args.get("jobOrderId"),
            limit=request.args.get("limit", 100),
        )
    )


@consumables_bp.route("/issues", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def issue_consumable():
    """Office Staff hand a consumable to a worker for the operation they are on."""
    return jsonify(issues.issue(request.get_json() or {}, get_current_user_id())), 201


@consumables_bp.route("/issues/<event_id>/undo", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def undo_issue(event_id):
    return jsonify(issues.undo_issue(event_id, get_current_user_id()))


@consumables_bp.route("/issue-options", methods=["GET"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def issue_options():
    return jsonify(issues.issue_options(request.args.get("workerId")))


@consumables_bp.route("/job/<job_id>/issues", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN, UserRole.OFFICE_STAFF, UserRole.PRODUCTION_WORKER)
def job_issues(job_id):
    """Consumables handed out for a job's operations; a worker sees only jobs
    they have work on."""
    from flask_jwt_extended import get_jwt

    from app.models.job_order import JobOrder
    from app.services.job_order_service import check_job_access
    from app.utils.errors import AppError

    job = JobOrder.query.get(job_id)
    if job is None:
        raise AppError("Job order not found", "NOT_FOUND", 404)
    check_job_access(job, get_current_user_id(), get_jwt().get("role"))
    return jsonify(issues.job_issues(job.id))


@consumables_bp.route("/requests", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN, UserRole.OFFICE_STAFF)
def list_requests():
    return jsonify([r.to_dict() for r in requests_svc.open_requests()])


@consumables_bp.route("/requests/<request_id>/dismiss", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def dismiss_request(request_id):
    return jsonify(requests_svc.dismiss(request_id, get_current_user_id()).to_dict())
