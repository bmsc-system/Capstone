from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.utils.row_locks import lock_row
from app.middleware.rbac import get_current_user_id, get_current_user_role, require_roles
from app.models.operation import JobOperation
from app.models.operation_time import MachineDowntime
from app.models.user import UserRole
from app.services import completion_estimate_service as estimate_service
from app.services import job_order_service as jo_service
from app.services import offline_action_service as offline_service
from app.services import operation_service as op_service
from app.utils.errors import AppError

operations_bp = Blueprint("operations", __name__)


@operations_bp.route("/mine", methods=["GET"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def my_operations():
    ops = op_service.list_my_operations(get_current_user_id())
    return jsonify(
        [op.to_dict(include_material_wait=True, for_worker=True) for op in ops]
    )


@operations_bp.route("/mine/materials", methods=["GET"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def my_materials():
    """Raw materials of the worker's open jobs: what, size, quantity, arrived or not."""
    from app.services.consumable_request_service import worker_materials

    return jsonify(worker_materials(get_current_user_id()))


@operations_bp.route("/<operation_id>/assign", methods=["PATCH"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def assign_operation(operation_id):
    operation = JobOperation.query.get(operation_id)
    if not operation:
        raise AppError("Operation not found", "NOT_FOUND", 404)
    data = request.get_json() or {}
    worker_id = data.get("assignedWorkerId")
    if not worker_id:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "assignedWorkerId required"}}), 400
    operation = jo_service.assign_operation_worker(
        operation, worker_id, data.get("helperIds") if "helperIds" in data else None
    )
    return jsonify(operation.to_dict())


def _worker_action(operation_id, action, apply):
    """Run a start/pause/resume/complete once per phone action id: a resent
    action returns the operation as it is now instead of recording again.
    ``apply`` also gets the time the server received the action."""
    received_at = datetime.now(timezone.utc)
    # Two crew members acting on one operation at once: the second waits, then
    # sees the first one's change (e.g. already completed).
    operation = lock_row(JobOperation, operation_id)
    if not operation:
        raise AppError("Operation not found", "NOT_FOUND", 404)
    data = request.get_json() or {}
    user_id = get_current_user_id()
    action_id = offline_service.client_action_id(data)
    if offline_service.previous_result(action_id, user_id, action):
        return jsonify(operation.to_dict())
    operation = apply(operation, data, user_id, get_current_user_role(), received_at)
    offline_service.record(action_id, user_id, action, operation.id)
    estimate_service.after_operation_change(operation)
    return jsonify(operation.to_dict())


@operations_bp.route("/<operation_id>/start", methods=["POST"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def start_operation(operation_id):
    return _worker_action(
        operation_id,
        "start",
        lambda op, data, uid, role, received_at: op_service.start_operation(
            op, uid, role, data.get("timestamp") or received_at, received_at=received_at
        ),
    )


@operations_bp.route("/<operation_id>/pause", methods=["POST"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def pause_operation(operation_id):
    data = request.get_json() or {}
    if not data.get("reason"):
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "reason is required"}}), 400
    return _worker_action(
        operation_id,
        "pause",
        lambda op, data, uid, role, received_at: op_service.pause_operation(
            op, uid, role, reason=data.get("reason"), note=data.get("note"),
            timestamp=data.get("timestamp") or received_at, received_at=received_at,
        ),
    )


@operations_bp.route("/<operation_id>/resume", methods=["POST"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def resume_operation(operation_id):
    return _worker_action(
        operation_id,
        "resume",
        lambda op, data, uid, role, received_at: op_service.resume_operation(
            op, uid, role, timestamp=data.get("timestamp") or received_at,
            received_at=received_at,
        ),
    )


@operations_bp.route("/<operation_id>/complete", methods=["POST"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def complete_operation(operation_id):
    return _worker_action(
        operation_id,
        "complete",
        lambda op, data, uid, role, received_at: op_service.complete_operation(
            op, uid, role, data.get("timestamp") or received_at, received_at=received_at
        ),
    )


@operations_bp.route("/<operation_id>/send-out", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def send_out_operation(operation_id):
    operation = JobOperation.query.get(operation_id)
    if not operation:
        raise AppError("Operation not found", "NOT_FOUND", 404)
    data = request.get_json() or {}
    operation = op_service.send_out_operation(
        operation,
        get_current_user_role(),
        data.get("sentOutDate"),
        data.get("sentTo"),
    )
    estimate_service.after_operation_change(operation)
    return jsonify(operation.to_dict())


@operations_bp.route("/<operation_id>/return", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def return_operation(operation_id):
    operation = JobOperation.query.get(operation_id)
    if not operation:
        raise AppError("Operation not found", "NOT_FOUND", 404)
    data = request.get_json() or {}
    operation = op_service.return_operation(operation, data.get("returnedDate"))
    estimate_service.after_operation_change(operation)
    return jsonify(operation.to_dict())


@operations_bp.route("/<operation_id>/rework", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def rework_operation(operation_id):
    operation = JobOperation.query.get(operation_id)
    if not operation:
        raise AppError("Operation not found", "NOT_FOUND", 404)

    data = request.get_json() or {}
    follow = op_service.create_rework_operation(
        operation,
        get_current_user_id(),
        get_current_user_role(),
        reason=data.get("reason") or data.get("note"),
        category=data.get("category") or data.get("reworkReasonCategory"),
    )
    return jsonify(follow.to_dict()), 201


@operations_bp.route("/machine-units/status", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def list_machine_unit_status():
    include_inactive = str(request.args.get("includeInactive", "")).lower() in (
        "1",
        "true",
        "yes",
    )
    from app.services.read_cache import get_or_build

    return jsonify(
        get_or_build(
            ("machine-unit-status", include_inactive),
            lambda: op_service.list_machine_unit_statuses(include_inactive=include_inactive),
            ttl=15,
        )
    )


@operations_bp.route("/machine-units", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def create_machine_unit():
    data = request.get_json() or {}
    unit = op_service.create_machine_unit(
        machine_type_id=data.get("machineTypeId") or data.get("machine_type_id"),
        label=data.get("label"),
    )
    return jsonify(unit.to_dict()), 201


@operations_bp.route("/machine-units/<unit_id>", methods=["PATCH"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def patch_machine_unit(unit_id):
    data = request.get_json() or {}
    if "active" in data:
        unit = op_service.set_machine_unit_active(unit_id, bool(data.get("active")))
        payload = unit.to_dict()
        if not unit.active:
            affected = op_service.list_affected_operations(unit_id)
            payload["affectedCount"] = len(affected)
            payload["affectedOperations"] = affected
        return jsonify(payload)
    if "defaultOperatorId" in data or "default_operator_id" in data:
        raw = data.get("defaultOperatorId", data.get("default_operator_id"))
        unit = op_service.set_machine_unit_default_operator(unit_id, raw)
        return jsonify(unit.to_dict())
    return jsonify(
        {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Provide active and/or defaultOperatorId",
            }
        }
    ), 400


@operations_bp.route("/machine-units/<unit_id>/downtime", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN, UserRole.PRODUCTION_WORKER)
def open_downtime(unit_id):
    received_at = datetime.now(timezone.utc)
    data = request.get_json() or {}
    user_id = get_current_user_id()
    action_id = offline_service.client_action_id(data)
    previous = offline_service.previous_result(action_id, user_id, "breakdown")
    if previous:
        row = MachineDowntime.query.get(previous)
        payload = row.to_dict() if row else {"id": previous}
        payload["affectedCount"] = 0
        payload["affectedOperations"] = []
        return jsonify(payload), 200
    row = op_service.open_machine_downtime(
        unit_id,
        reported_by_id=get_current_user_id(),
        category=data.get("category") or data.get("reason"),
        note=data.get("note"),
        started_at=data.get("startedAt") or data.get("started_at"),
        operation_id=data.get("operationId"),
        job_order_id=data.get("jobOrderId"),
        reporter_role=get_current_user_role(),
        expected_repair_date=data.get("expectedRepairDate"),
        received_at=received_at,
    )
    offline_service.record(action_id, user_id, "breakdown", row.id)
    payload = row.to_dict()
    affected = op_service.list_affected_operations(unit_id)
    payload["affectedCount"] = len(affected)
    payload["affectedOperations"] = affected
    if get_current_user_role() == UserRole.ADMIN.value:
        from app.services.shop_floor_check_service import breakdown_options

        payload["work"] = breakdown_options(unit_id)
    return jsonify(payload), 201


@operations_bp.route("/machine-units/<unit_id>/downtime/work", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def down_unit_work(unit_id):
    """Unstarted work booked on the unit while it is down, and what can be done with it."""
    from app.services.shop_floor_check_service import breakdown_options

    return jsonify(breakdown_options(unit_id))


@operations_bp.route("/machine-units/<unit_id>/downtime/work", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def move_down_unit_work(unit_id):
    """action: MOVE_UNIT (another free unit of the type), AFTER_REPAIR or LEAVE."""
    from app.services.shop_floor_check_service import resolve_breakdown

    from app.services.completion_estimate_service import check_released_jobs

    data = request.get_json() or {}
    result = resolve_breakdown(unit_id, data.get("action"), actor_id=get_current_user_id())
    # Moved work can push jobs late: staff alerts and client Delayed emails.
    check_released_jobs()
    return jsonify(result)


@operations_bp.route("/machine-units/downtime/<downtime_id>/close", methods=["POST"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def close_downtime(downtime_id):
    data = request.get_json() or {}
    row = op_service.close_machine_downtime(
        downtime_id,
        ended_at=data.get("endedAt") or data.get("ended_at"),
        note=data.get("note"),
        actor_id=get_current_user_id(),
        actor_role=get_current_user_role(),
    )
    return jsonify(row.to_dict())


@operations_bp.route("/machine-units/downtime/<downtime_id>", methods=["PATCH"])
@jwt_required()
@require_roles(UserRole.ADMIN)
def update_downtime(downtime_id):
    data = request.get_json() or {}
    if "expectedRepairDate" not in data:
        return jsonify(
            {"error": {"code": "VALIDATION_ERROR", "message": "Provide expectedRepairDate"}}
        ), 400
    row = op_service.set_downtime_expected_repair(downtime_id, data.get("expectedRepairDate"))
    return jsonify(row.to_dict())


@operations_bp.route("/machine-units/<unit_id>/downtime", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN, UserRole.OFFICE_STAFF)
def list_unit_downtime(unit_id):
    rows = (
        MachineDowntime.query.filter_by(machine_unit_id=unit_id)
        .order_by(MachineDowntime.started_at.desc())
        .all()
    )
    return jsonify([r.to_dict() for r in rows])
