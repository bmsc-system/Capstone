"""Analytics endpoints, split by role (objective 4.1).

Administrator: production (overview, efficiency by worker / operation type /
machine, trend, delays and the Pareto, expected machine workload), plus the
transaction analytics. Office Staff: transactions only (job orders and
deliveries, sales and forecasts, demand, purchasing, consumable replenishment) and
never any per-worker figures. Production worker: only their own summary.
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.middleware.rbac import get_current_user_id, require_roles
from app.models.user import UserRole
from app.services import analytics_service as analytics

analytics_bp = Blueprint("analytics", __name__)

PRODUCTION = (UserRole.ADMIN,)
TRANSACTIONS = (UserRole.ADMIN, UserRole.OFFICE_STAFF)


def _min_ops_arg():
    raw = request.args.get("minOps")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        from app.utils.errors import AppError

        raise AppError("minOps must be an integer", "VALIDATION_ERROR", 400)


# Analytics change only when records change, and every committed write clears
# the cache, so results can be kept for a few minutes. On a small server
# (Render's free plan) rebuilding the analytics overview takes seconds.
ANALYTICS_TTL = 300


def _cached(name, builder, ttl=ANALYTICS_TTL, **key):
    from app.services.read_cache import get_or_build

    cache_key = (name, tuple(sorted((k, None if v is None else str(v)) for k, v in key.items())))
    return jsonify(get_or_build(cache_key, builder, ttl=ttl))


def _period():
    return {"from_s": request.args.get("from"), "to_s": request.args.get("to")}


@analytics_bp.route("/overview", methods=["GET"])
@jwt_required()
@require_roles(*PRODUCTION)
def overview():
    period = _period()
    return _cached("overview", lambda: analytics.overview(**period), **period)


@analytics_bp.route("/efficiency/by-worker", methods=["GET"])
@jwt_required()
@require_roles(*PRODUCTION)
def by_worker():
    return jsonify(analytics.efficiency_by_worker(**_period(), min_ops=_min_ops_arg()))


@analytics_bp.route("/efficiency/by-operation-type", methods=["GET"])
@jwt_required()
@require_roles(*PRODUCTION)
def by_operation_type():
    return jsonify(analytics.efficiency_by_operation_type(**_period(), min_ops=_min_ops_arg()))


@analytics_bp.route("/efficiency/by-machine", methods=["GET"])
@jwt_required()
@require_roles(*PRODUCTION)
def by_machine():
    return jsonify(analytics.efficiency_by_machine(**_period(), min_ops=_min_ops_arg()))


@analytics_bp.route("/efficiency/trend", methods=["GET"])
@jwt_required()
@require_roles(*PRODUCTION)
def trend():
    period = _period()
    return _cached("efficiency-trend", lambda: analytics.efficiency_trend(**period), **period)


@analytics_bp.route("/delays", methods=["GET"])
@jwt_required()
@require_roles(*PRODUCTION)
def delays():
    period = _period()
    return _cached("delays", lambda: analytics.delays(**period), **period)


@analytics_bp.route("/utilization/weekly", methods=["GET"])
@jwt_required()
@require_roles(*PRODUCTION)
def utilization_weekly():
    return jsonify(analytics.utilization_by_week(**_period()))


@analytics_bp.route("/completion-estimates", methods=["GET"])
@jwt_required()
@require_roles(*PRODUCTION)
def completion_estimates():
    return _cached("completion-estimates", analytics.completion_estimates)


@analytics_bp.route("/demand/capacity", methods=["GET"])
@jwt_required()
@require_roles(*PRODUCTION)
def demand_capacity():
    return jsonify(analytics.demand_capacity(**_period()))


@analytics_bp.route("/job-orders", methods=["GET"])
@jwt_required()
@require_roles(*TRANSACTIONS)
def job_orders_summary():
    period = _period()
    return _cached("job-orders", lambda: analytics.job_orders_summary(**period), **period)


@analytics_bp.route("/sales/summary", methods=["GET"])
@jwt_required()
@require_roles(*TRANSACTIONS)
def sales_summary():
    period = _period()
    return _cached("sales-summary", lambda: analytics.sales_summary(**period), **period)


@analytics_bp.route("/sales/forecast", methods=["GET"])
@jwt_required()
@require_roles(*TRANSACTIONS)
def sales_forecast():
    period = _period()
    months = request.args.get("months")
    return _cached(
        "sales-forecast", lambda: analytics.sales_forecast(**period, months=months), months=months, **period
    )


@analytics_bp.route("/demand/forecast", methods=["GET"])
@jwt_required()
@require_roles(*TRANSACTIONS)
def demand_forecast():
    months = request.args.get("months")
    return _cached("demand-forecast", lambda: analytics.demand_forecast(months=months), months=months)


@analytics_bp.route("/consumables/replenishment", methods=["GET"])
@jwt_required()
@require_roles(*TRANSACTIONS)
def consumable_replenishment():
    return _cached("consumable-replenishment", analytics.consumable_replenishment)


@analytics_bp.route("/purchasing", methods=["GET"])
@jwt_required()
@require_roles(*TRANSACTIONS)
def purchasing():
    return jsonify(analytics.purchasing_summary(**_period()))


@analytics_bp.route("/inventory", methods=["GET"])
@jwt_required()
@require_roles(*TRANSACTIONS)
def inventory():
    """Consumable use and job-material stock for the period (Inventory tab)."""
    from app.services.inventory_analytics_service import inventory_summary

    period = _period()
    return _cached("inventory", lambda: inventory_summary(**period), **period)


@analytics_bp.route("/me", methods=["GET"])
@jwt_required()
@require_roles(UserRole.PRODUCTION_WORKER)
def my_summary():
    return jsonify(analytics.my_summary(get_current_user_id()))


@analytics_bp.route("/office-summary", methods=["GET"])
@jwt_required()
@require_roles(UserRole.OFFICE_STAFF)
def office_summary():
    """The Office Staff dashboard: office work waiting to be done."""
    from app.services.office_dashboard_service import office_summary as build

    return jsonify(build())
