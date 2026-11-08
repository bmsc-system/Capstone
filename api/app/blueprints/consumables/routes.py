"""Consumable purchases (recorded by the Office Staff, read by the Admin too) and
running-low reports from the shop floor."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.middleware.rbac import get_current_user_id, require_roles
from app.models.user import UserRole
from app.services import consumable_purchase_service as purchases
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
    """Names and sizes of the consumables, with any running-low report. No prices."""
    return jsonify(requests_svc.worker_consumables(get_current_user_id()))


@consumables_bp.route("/<tool_id>/report", methods=["POST"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def report_low(tool_id):
    data = request.get_json() or {}
    req = requests_svc.report(tool_id, data.get("level"), get_current_user_id())
    return jsonify(req.to_dict(get_current_user_id())), 201


@consumables_bp.route("/requests", methods=["GET"])
@jwt_required()
@require_roles(UserRole.ADMIN, UserRole.OFFICE_STAFF)
def list_requests():
    return jsonify([r.to_dict() for r in requests_svc.open_requests()])


@consumables_bp.route("/requests/<request_id>/withdraw", methods=["POST"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def withdraw_request(request_id):
    req = requests_svc.withdraw(request_id, get_current_user_id())
    return jsonify(req.to_dict(get_current_user_id()))


@consumables_bp.route("/requests/<request_id>/dismiss", methods=["POST"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def dismiss_request(request_id):
    return jsonify(requests_svc.dismiss(request_id, get_current_user_id()).to_dict())
