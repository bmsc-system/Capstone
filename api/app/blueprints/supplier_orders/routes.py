from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.middleware.rbac import get_current_user_id, require_roles
from app.models.user import UserRole
from app.services import supplier_order_service as svc

supplier_orders_bp = Blueprint("supplier_orders", __name__)

_STAFF = (UserRole.ADMIN, UserRole.OFFICE_STAFF)


@supplier_orders_bp.route("", methods=["GET"])
@jwt_required()
@require_roles(*_STAFF)
def list_orders():
    rows = svc.list_orders(
        status=request.args.get("status") or None,
        supplier_id=request.args.get("supplierId") or None,
    )
    return jsonify([o.to_dict() for o in rows])


@supplier_orders_bp.route("/overdue-check", methods=["POST"])
@jwt_required()
@require_roles(*_STAFF)
def overdue_check():
    from flask import current_app

    from app.services.overdue_delivery_service import request_checks

    if current_app.config.get("TESTING"):
        return jsonify({"started": False}), 202
    return jsonify({"started": request_checks(current_app._get_current_object())}), 202


@supplier_orders_bp.route("/outstanding", methods=["GET"])
@jwt_required()
@require_roles(*_STAFF)
def outstanding():
    return jsonify(svc.ordering_context(job_id=request.args.get("jobId") or None))


@supplier_orders_bp.route("/draft-lines", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def add_draft_lines():
    data = request.get_json() or {}
    order, _ = svc.add_lines_to_draft(
        data.get("supplierId"), data.get("lines") or [], get_current_user_id()
    )
    return jsonify(order.to_dict(include_lines=True))


@supplier_orders_bp.route("/<order_id>", methods=["GET"])
@jwt_required()
@require_roles(*_STAFF)
def get_order(order_id):
    return jsonify(_detail(svc.get_order(order_id)))


def _detail(order):
    data = order.to_dict(include_lines=True)
    data["expectedDeliveryChanges"] = svc.expected_delivery_history(order)
    return data


@supplier_orders_bp.route("/<order_id>/expected-delivery", methods=["PATCH"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def change_expected_delivery(order_id):
    data = request.get_json() or {}
    result = svc.change_expected_delivery(
        svc.get_order(order_id), data.get("expectedDeliveryDate"), data.get("note")
    )
    body = _detail(result["order"])
    body["movedJobs"] = result["movedJobs"]
    body["notMovedJobs"] = result["notMovedJobs"]
    return jsonify(body)


@supplier_orders_bp.route("/<order_id>", methods=["PATCH"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def update_order(order_id):
    order = svc.update_draft(svc.get_order(order_id), request.get_json() or {})
    return jsonify(order.to_dict(include_lines=True))


@supplier_orders_bp.route("/<order_id>/issue", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def issue(order_id):
    data = request.get_json() or {}
    order = svc.issue_order(
        svc.get_order(order_id),
        get_current_user_id(),
        data.get("dateIssued"),
        po_seq=data.get("poSeq"),
    )
    return jsonify(order.to_dict(include_lines=True))


@supplier_orders_bp.route("/<order_id>/cancel", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def cancel(order_id):
    order = svc.cancel_order(svc.get_order(order_id), get_current_user_id())
    return jsonify(order.to_dict(include_lines=True))


@supplier_orders_bp.route("/<order_id>/receive", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def receive(order_id):
    data = request.get_json() or {}
    order = svc.receive_order(
        svc.get_order(order_id), data.get("receivedDate"), actor_id=get_current_user_id()
    )
    return jsonify(order.to_dict(include_lines=True))


@supplier_orders_bp.route("/<order_id>/print", methods=["GET"])
@jwt_required()
@require_roles(*_STAFF)
def print_order(order_id):
    """Read the print payload only; POST /print records the print."""
    return jsonify(svc.print_data(svc.get_order(order_id)))


@supplier_orders_bp.route("/<order_id>/print", methods=["POST"])
@supplier_orders_bp.route("/<order_id>/mark-printed", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def mark_printed(order_id):
    """Record that the PO was printed (required before receive); returns the
    same payload as GET /print so the UI can open the print view."""
    return jsonify(svc.mark_printed(svc.get_order(order_id), get_current_user_id()))


@supplier_orders_bp.route("/<order_id>/lines/<line_id>", methods=["PATCH"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def update_line(order_id, line_id):
    order = svc.get_order(order_id)
    svc.update_draft_line(order, svc.get_line(order, line_id), request.get_json() or {})
    return jsonify(order.to_dict(include_lines=True))


@supplier_orders_bp.route("/<order_id>/lines/<line_id>", methods=["DELETE"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def remove_line(order_id, line_id):
    order = svc.get_order(order_id)
    svc.remove_draft_line(order, svc.get_line(order, line_id))
    return jsonify(order.to_dict(include_lines=True))
