from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.middleware.rbac import require_roles
from app.models.user import User, UserRole
from app.services.scoring_service import recommendation_weights
from app.services.worker_availability import get_busy_workers
from app.services.worker_profile_service import (
    machine_skill_holders,
    query_assignable_workers,
)
from app.services.worker_suggestion_service import suggest_workers

workers_bp = Blueprint("workers", __name__)


@workers_bp.route("", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def list_workers():
    exclude_operation_id = request.args.get("excludeOperationId")
    scheduled_start = request.args.get("scheduledStart")
    scheduled_end = request.args.get("scheduledEnd")
    machine_type_id = request.args.get("machineTypeId")
    busy = get_busy_workers(
        start=scheduled_start,
        end=scheduled_end,
        exclude_operation_id=exclude_operation_id,
    )
    # Workers and Admins; a machine operation lists only its skill holders
    # once anyone has that skill recorded.
    query = query_assignable_workers()
    holders = machine_skill_holders(machine_type_id)
    if holders is not None:
        query = query.filter(User.id.in_(list(holders)))
    workers = query.all()
    result = []
    for w in workers:
        data = w.to_dict(include_profile=True, include_skills=True)
        conflict = busy.get(w.id)
        data["available"] = conflict is None
        if conflict:
            data["activeJobId"] = conflict.job_order_id
            data["activeJobTitle"] = conflict.operation_name
            data["conflictOperationId"] = conflict.id
        result.append(data)
    return jsonify(result)


@workers_bp.route("/suggest", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def suggest():
    data = request.get_json() or {}
    operations = data.get("operations", [])
    machine_type_id = data.get("machineTypeId")
    operation_type_id = data.get("operationTypeId")
    operation_name = data.get("operationName")

    if not operations and not machine_type_id and not operation_type_id and not operation_name:
        return jsonify(
            {"error": {"code": "VALIDATION_ERROR", "message": "operations or machineTypeId required"}}
        ), 400

    result = suggest_workers(
        operations,
        exclude_job_id=data.get("excludeJobId"),
        scheduled_start=data.get("scheduledStart"),
        scheduled_end=data.get("scheduledEnd"),
        exclude_operation_id=data.get("excludeOperationId"),
        machine_type_id=machine_type_id,
        operation_type_id=operation_type_id,
        operation_name=operation_name,
        lead_id=data.get("leadId"),
        exclude_worker_ids=data.get("excludeWorkerIds") or [],
        pairs=bool(data.get("pairs")),
        estimated_hours=data.get("estimatedHours"),
        hours_before=data.get("hoursBefore"),
        hours_after=data.get("hoursAfter"),
        steps_before=data.get("stepsBefore") if isinstance(data.get("stepsBefore"), list) else None,
    )
    result["recommendationWeights"] = recommendation_weights()
    return jsonify(result)
