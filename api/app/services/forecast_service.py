"""Monthly sales and job-order demand forecasts (3-month moving average, simple
exponential smoothing and Holt's trend, picked by back-test error; see
``forecast_methods``), and consumable run-out dates with reorder points."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, time, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.job_order import JobOrder, JobOrderStatus, JobType
from app.models.operation import OperationStatus
from app.models.tool import Tool, ToolCategory
from app.services import forecast_methods as fm
from app.services.schedule_calendar import (
    SHOP_TZ,
    default_shop_schedule_by_dow,
    effective_windows_for_date,
    is_shop_working_day,
    load_calendar_exceptions,
    shop_local_to_utc,
    shop_now,
    shop_working_day_memo,
)

MONTHLY_WINDOW = 3
# 3 months to fit the first forecast plus 3 back-tested months to compare methods.
MIN_FORECAST_MONTHS = fm.MIN_TRAIN + 3
FORECAST_HORIZON_MONTHS = 3
# Months ahead a user can ask the sales and demand forecasts for.
FORECAST_HORIZON_CHOICES = (1, 3, 6, 12)


def horizon_months(value) -> int:
    """A forecast horizon from a query string; anything else gives the default."""
    try:
        months = int(value)
    except (TypeError, ValueError):
        return FORECAST_HORIZON_MONTHS
    return months if months in FORECAST_HORIZON_CHOICES else FORECAST_HORIZON_MONTHS


STOCKTAKE_WINDOW = 3
NOT_ENOUGH_HISTORY = "Not enough history yet"
NOT_ENOUGH_DATA = "Not enough data"


def _round(v, places=2):
    return None if v is None else round(float(v), places)


# --- Method ------------------------------------------------------------------


def moving_average(values, window=MONTHLY_WINDOW):
    """Average of the last ``window`` values, or None when there are fewer."""
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def backtest(values, window=MONTHLY_WINDOW):
    """Forecast every value that has ``window`` earlier values and compare.

    Returns (rows, mae, mape). MAPE skips periods whose actual value is 0,
    since a percentage of zero is undefined; it is None when every period is 0.
    """
    rows = []
    for i in range(window, len(values)):
        forecast = sum(values[i - window : i]) / window
        actual = values[i]
        error = abs(actual - forecast)
        rows.append(
            {
                "index": i,
                "forecast": forecast,
                "actual": actual,
                "absoluteError": error,
                "absolutePctError": (error / abs(actual) * 100.0) if actual else None,
            }
        )
    if not rows:
        return rows, None, None
    mae = sum(r["absoluteError"] for r in rows) / len(rows)
    pcts = [r["absolutePctError"] for r in rows if r["absolutePctError"] is not None]
    mape = (sum(pcts) / len(pcts)) if pcts else None
    return rows, mae, mape


# --- Monthly series ----------------------------------------------------------


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def monthly_series(dated_values, current_month: date):
    """[(month_start, total)] from the first month with data through the last
    complete month before ``current_month``; months with nothing are 0."""
    totals = defaultdict(float)
    for on, value in dated_values:
        m = _month_start(on)
        if m < current_month:
            totals[m] += float(value)
    if not totals:
        return []
    out = []
    m = min(totals)
    while m < current_month:
        out.append((m, totals.get(m, 0.0)))
        m = _next_month(m)
    return out


def first_full_month(history_start: date) -> date:
    """The first calendar month fully inside the history.

    The month the records begin in counts only when they begin on its first
    working day (Monday to Saturday); otherwise it is partial and left out.
    """
    first = _month_start(history_start)
    first_working = first
    while first_working.weekday() == 6:
        first_working += timedelta(days=1)
    return first if history_start <= first_working else _next_month(first)


def full_month_series(dated_values, current_month: date, start_month: date):
    """[(month_start, total)] for every complete month from ``start_month`` up to
    the month before ``current_month``; months with nothing are 0."""
    totals = defaultdict(float)
    for on, value in dated_values:
        m = _month_start(on)
        if start_month <= m < current_month:
            totals[m] += float(value)
    out = []
    m = start_month
    while m < current_month:
        out.append((m, totals.get(m, 0.0)))
        m = _next_month(m)
    return out


def monthly_forecast(
    dated_values,
    today: date | None = None,
    window=MONTHLY_WINDOW,
    places=2,
    history_start: date | None = None,
    horizon=FORECAST_HORIZON_MONTHS,
):
    """Forecast of the current month and the next ones from complete past months.

    The three methods in ``forecast_methods`` are back-tested on the same
    months and the one with the lowest MAPE (MAE when MAPE is undefined) is
    used. The current month is in progress and the first month of the history
    is usually partial; neither is used as history.
    """
    today = today or shop_now().date()
    current = _month_start(today)
    dated_values = list(dated_values)
    start = history_start or (min(on for on, _ in dated_values) if dated_values else None)
    start_month = first_full_month(start) if start else current
    series = full_month_series(dated_values, current, start_month) if start else []
    values = [v for _, v in series]
    so_far = sum(float(v) for on, v in dated_values if _month_start(on) == current and on <= today)

    excluded = []
    if start and _month_start(start) < start_month:
        excluded.append({"month": _month_key(_month_start(start)), "reason": "PARTIAL_FIRST_MONTH"})
    excluded.append({"month": _month_key(current), "reason": "IN_PROGRESS"})

    enough = len(series) >= MIN_FORECAST_MONTHS
    results = fm.evaluate(values) if enough else {}
    chosen, metric = fm.select_method(results) if enough else (None, None)
    best = results.get(chosen) if chosen else None
    enough = enough and best is not None

    horizon_rows = []
    if enough:
        for h in range(1, horizon + 1):
            point = max(fm.forecast(chosen, values, h), 0.0)
            lower, upper = fm.interval(point, best["rmse"], h)
            m = current
            for _ in range(h - 1):
                m = _next_month(m)
            horizon_rows.append(
                {
                    "month": _month_key(m),
                    "forecast": _round(point, places),
                    "lower": _round(lower, places),
                    "upper": _round(upper, places),
                }
            )
    by_index = {r["index"]: r for r in best["rows"]} if best else {}

    return {
        "method": fm.METHOD_NAMES[chosen] if enough else None,
        "methodKey": chosen if enough else None,
        "errorMetric": metric if enough else None,
        "parameters": fm.parameters(chosen, values) if enough else {},
        "window": window,
        "monthsAvailable": len(series),
        "monthsNeeded": MIN_FORECAST_MONTHS,
        "enoughHistory": enough,
        "notEnoughHistoryNote": None if enough else NOT_ENOUGH_HISTORY,
        "forecastMonth": _month_key(current),
        "forecast": horizon_rows[0]["forecast"] if enough else None,
        "mae": _round(best["mae"], places) if enough else None,
        "mapePct": _round(best["mapePct"], 1) if enough else None,
        "backtestedMonths": best["backtestedMonths"] if enough else 0,
        "intervalLevelPct": fm.INTERVAL_LEVEL,
        "horizon": horizon_rows,
        "currentMonthSoFar": _round(so_far, places),
        "excludedMonths": excluded,
        "methods": [
            {
                "key": m,
                "name": fm.METHOD_NAMES[m],
                "mae": _round(results[m]["mae"], places),
                "mapePct": _round(results[m]["mapePct"], 1),
                "backtestedMonths": results[m]["backtestedMonths"],
                "selected": m == chosen,
            }
            for m in fm.METHOD_ORDER
        ]
        if enough
        else [],
        "months": [
            {
                "month": _month_key(m),
                "actual": _round(v, places),
                "forecast": _round(by_index[i]["forecast"], places) if i in by_index else None,
                "absoluteError": (
                    _round(abs(by_index[i]["actual"] - by_index[i]["forecast"]), places)
                    if i in by_index
                    else None
                ),
            }
            for i, (m, v) in enumerate(series)
        ],
    }


def _job_completion_date(job) -> date | None:
    ends = [
        o.actual_end
        for o in (job.operations or [])
        if o.actual_end and o.status == OperationStatus.COMPLETED
    ]
    return max(ends).astimezone(SHOP_TZ).date() if ends else None


def _job_received_date(job) -> date:
    if job.po_date:
        return job.po_date
    return job.created_at.astimezone(SHOP_TZ).date()


def sales_forecast(today: date | None = None, horizon: int = FORECAST_HORIZON_MONTHS):
    """Monthly income from finished jobs (Completed, For Delivery, Delivered), by completion month."""
    jobs = (
        JobOrder.query.options(joinedload(JobOrder.operations))
        .filter(
            JobOrder.status.in_(
                (JobOrderStatus.COMPLETED, JobOrderStatus.FOR_DELIVERY, JobOrderStatus.DELIVERED)
            )
        )
        .all()
    )
    dated = []
    for job in jobs:
        done = _job_completion_date(job)
        if done is not None:
            dated.append((done, float(job.amount or 0)))
    return {
        "label": "salesForecast",
        "description": "Monthly income from finished jobs (Completed, For Delivery or Delivered), by completion month.",
        **monthly_forecast(dated, today, horizon=horizon),
    }


def demand_forecast(today: date | None = None, horizon: int = FORECAST_HORIZON_MONTHS):
    """Monthly job orders received (PO date, else creation date), overall and by job type.

    Every job type uses the overall start of the history, so a type with no
    orders in the early months counts them as 0 instead of starting later.
    """
    jobs = JobOrder.query.filter(JobOrder.status != JobOrderStatus.CANCELLED).all()
    overall = []
    by_type = defaultdict(list)
    for job in jobs:
        on = _job_received_date(job)
        overall.append((on, 1))
        by_type[job.job_type.value if job.job_type else "UNSPECIFIED"].append((on, 1))
    start = min((on for on, _ in overall), default=None)
    result = {
        "label": "demandForecast",
        "description": "Job orders received per month, by PO date (creation date when there is none).",
        **monthly_forecast(overall, today, places=1, history_start=start, horizon=horizon),
    }
    result["byJobType"] = [
        {
            "jobType": jt.value,
            **monthly_forecast(
                by_type.get(jt.value, []), today, places=1, history_start=start, horizon=horizon
            ),
        }
        for jt in JobType
    ]
    return result


# --- Consumable run-out --------------------------------------------------------


def shop_working_dates_after(start: date, count: int, schedule=None, exceptions=None):
    """The next ``count`` shop working days strictly after ``start``."""
    schedule = schedule or default_shop_schedule_by_dow()
    memo = shop_working_day_memo() if exceptions is None else None
    out = []
    d = start
    span = max(14, count * 2 + 14)
    while len(out) < count:
        if exceptions is None:
            chunk = load_calendar_exceptions(d + timedelta(days=1), d + timedelta(days=span))
        else:
            chunk = exceptions
        end = d + timedelta(days=span)
        while d < end and len(out) < count:
            d += timedelta(days=1)
            if is_shop_working_day(d, schedule, chunk, memo):
                out.append(d)
    return out


def shop_working_days_between(after: date, through: date, schedule=None, exceptions=None) -> int:
    """Shop working days in (after, through]."""
    if through <= after:
        return 0
    schedule = schedule or default_shop_schedule_by_dow()
    memo = None
    if exceptions is None:
        exceptions = load_calendar_exceptions(after + timedelta(days=1), through)
        memo = shop_working_day_memo()
    n = 0
    d = after
    while d < through:
        d += timedelta(days=1)
        if is_shop_working_day(d, schedule, exceptions, memo):
            n += 1
    return n


def run_out_date(on_hand: float, daily_usage: float, start: date, schedule=None, exceptions=None):
    """(days_left, run_out_date) counting shop working days forward from ``start``.

    The stock lasts ``on_hand / daily_usage`` working days after ``start``;
    it runs out on the working day in which that total is reached.
    """
    if daily_usage is None or daily_usage <= 0:
        return None, None
    days_left = max(float(on_hand), 0.0) / float(daily_usage)
    if days_left == 0:
        return 0.0, start
    nth = math.ceil(days_left)
    return days_left, shop_working_dates_after(start, nth, schedule, exceptions)[-1]


# --- Reorder point (scan-based) ------------------------------------------------

DEFAULT_LEAD_TIME_DAYS = 7
ADU_WINDOW_MAX = 30
ADU_COMPUTED_MIN_DAYS = 14
REVIEW_PERIOD_DAYS = 30
ABC_LOOKBACK_DAYS = 90
STOCK_OK = "OK"
STOCK_REORDER_SOON = "REORDER_SOON"
STOCK_REORDER = "REORDER_NOW"
STOCK_NO_DATA = "NO_DATA"
LABEL_COMPUTED = "Computed"
LABEL_ESTIMATED = "Estimated"
LABEL_NO_DATA = "No data yet"

# Kept for older unit tests that import the z×SD helpers; production uses max-based safety.
SERVICE_LEVEL_Z = 1.65
SERVICE_LEVEL_PCT = 95


def safety_stock_scan(adu, max_du, avg_lead_wd, max_lead_wd):
    """max(0, MaxDU × MaxLead − ADU × AvgLead) in working-day units."""
    if adu is None:
        return 0.0
    return max(
        0.0,
        float(max_du or 0) * float(max_lead_wd or 0)
        - float(adu) * float(avg_lead_wd or 0),
    )


def safety_stock(daily_usages, lead_working_days, z=SERVICE_LEVEL_Z):
    """Legacy z×SD×√L (tests). Prefer safety_stock_scan for live forecasts."""
    n = len(daily_usages)
    if n < 2 or not lead_working_days or lead_working_days <= 0:
        return 0.0
    mean = sum(daily_usages) / n
    sd = math.sqrt(sum((u - mean) ** 2 for u in daily_usages) / (n - 1))
    return z * sd * math.sqrt(lead_working_days)


def reorder_point(daily_usage, lead_working_days, safety=0.0):
    """ADU × average lead (working days) + safety stock."""
    if daily_usage is None:
        return None
    return float(daily_usage) * float(lead_working_days or 0) + float(safety or 0)


def _reorder_soon_days():
    from app.models.shop_settings import ShopSettings

    row = ShopSettings.query.get(1)
    if row and row.reorder_soon_working_days:
        return int(row.reorder_soon_working_days)
    return 7


def stock_status(on_hand, adu, reorder_pt, days_to_rop, soon_days):
    if adu is None and reorder_pt is None:
        return STOCK_NO_DATA
    if on_hand is None:
        return STOCK_NO_DATA
    if reorder_pt is not None and on_hand <= reorder_pt:
        return STOCK_REORDER
    if days_to_rop is not None and days_to_rop <= soon_days:
        return STOCK_REORDER_SOON
    return STOCK_OK


def _calendar_to_working_lead(ordered: date, received: date, schedule) -> int:
    """Working days in (ordered, received] — calendar span converted via shop calendar."""
    return shop_working_days_between(ordered, received, schedule)


def _restock_lead_times(schedule=None):
    """Per tool: measured lead in shop working days, plus stated supplier calendar days."""
    from app.models.material_purchase import MaterialPurchase

    schedule = schedule or default_shop_schedule_by_dow()
    out = defaultdict(lambda: {"measured_wd": [], "stated": None, "last": None, "qty_received": 0.0, "first_order": None, "last_receive": None})
    lines = (
        MaterialPurchase.query.options(joinedload(MaterialPurchase.supplier))
        .filter(MaterialPurchase.tool_id.isnot(None), MaterialPurchase.cancelled_at.is_(None))
        .all()
    )
    for line in lines:
        row = out[line.tool_id]
        if line.date_ordered and line.date_received and line.date_received >= line.date_ordered:
            wd = _calendar_to_working_lead(line.date_ordered, line.date_received, schedule)
            if wd > 0:
                row["measured_wd"].append(wd)
            row["qty_received"] += float(line.quantity or 0)
            if row["first_order"] is None or line.date_ordered < row["first_order"]:
                row["first_order"] = line.date_ordered
            if row["last_receive"] is None or line.date_received > row["last_receive"]:
                row["last_receive"] = line.date_received
        on = line.date_ordered or (line.created_at.date() if line.created_at else None)
        if line.supplier and line.supplier.typical_lead_time_days is not None:
            if row["last"] is None or (on and on >= row["last"]):
                row["last"] = on
                row["stated"] = line.supplier.typical_lead_time_days
    return out


def lead_times_working(info, today: date, schedule):
    """(avg_wd, max_wd, source) — all in shop working days."""
    if info and info["measured_wd"]:
        measured = info["measured_wd"]
        return sum(measured) / len(measured), max(measured), "MEASURED"
    if info and info["stated"] is not None:
        cal = float(info["stated"])
        wd = shop_working_days_between(today, today + timedelta(days=math.ceil(cal)), schedule)
        return float(wd or cal), float(wd or cal), "SUPPLIER"
    wd = shop_working_days_between(
        today, today + timedelta(days=DEFAULT_LEAD_TIME_DAYS), schedule
    )
    return float(wd or DEFAULT_LEAD_TIME_DAYS), float(wd or DEFAULT_LEAD_TIME_DAYS), "DEFAULT"


def lead_time_for(info):
    """Legacy helper: average measured calendar-equivalent; prefer lead_times_working."""
    if info and info.get("measured_wd"):
        return sum(info["measured_wd"]) / len(info["measured_wd"]), "MEASURED"
    if info and info.get("stated") is not None:
        return float(info["stated"]), "SUPPLIER"
    return float(DEFAULT_LEAD_TIME_DAYS), "DEFAULT"


def _scan_usage_by_day_batch(tool_ids, start: date, end: date, schedule):
    """{tool_id: {shop date: units scanned}} for non-undone SCAN events — one query."""
    from app.models.tool_event import ToolEvent, ToolEventType

    out: dict[str, dict] = {tid: defaultdict(float) for tid in tool_ids}
    if not tool_ids or start > end:
        return out
    start_utc = shop_local_to_utc(start, time(0, 0))
    end_utc = shop_local_to_utc(end + timedelta(days=1), time(0, 0))
    filters = (
        ToolEvent.tool_id.in_(list(tool_ids)),
        ToolEvent.type == ToolEventType.SCAN,
        ToolEvent.undone_at.is_(None),
        ToolEvent.created_at >= start_utc,
        ToolEvent.created_at < end_utc,
    )
    qty = func.coalesce(func.nullif(ToolEvent.quantity, 0), 1)  # as `quantity or 1`
    if db.session.get_bind().dialect.name == "postgresql":
        # Summed per tool and shop day in the database: scans can number tens of thousands.
        shop_day = func.date(func.timezone(SHOP_TZ.key, ToolEvent.created_at))
        rows = (
            db.session.query(ToolEvent.tool_id, shop_day, func.sum(qty))
            .filter(*filters)
            .group_by(ToolEvent.tool_id, shop_day)
            .all()
        )
    else:
        rows = [
            (tid, at.astimezone(SHOP_TZ).date() if at.tzinfo else at.date(), q)
            for tid, at, q in db.session.query(ToolEvent.tool_id, ToolEvent.created_at, qty)
            .filter(*filters)
            .all()
        ]
    exceptions = load_calendar_exceptions(start, end)
    memo = shop_working_day_memo()
    if memo is None:
        memo = {} if getattr(schedule, "is_default_shop_week", False) else None
    for tool_id, local, quantity in rows:
        if is_shop_working_day(local, schedule, exceptions, memo):
            out[tool_id][local] += float(quantity or 0)
    return out


def _scan_usage_by_day(tool_id: str, start: date, end: date, schedule):
    """{shop date: units scanned} for one tool (thin wrapper over the batch query)."""
    return _scan_usage_by_day_batch([tool_id], start, end, schedule).get(tool_id, {})


def _first_scan_dates_batch(tool_ids):
    """{tool_id: first scan shop-date} for every id that has at least one scan."""
    from sqlalchemy import func

    from app.models.tool_event import ToolEvent, ToolEventType

    if not tool_ids:
        return {}
    rows = (
        ToolEvent.query.with_entities(
            ToolEvent.tool_id, func.min(ToolEvent.created_at)
        )
        .filter(
            ToolEvent.tool_id.in_(list(tool_ids)),
            ToolEvent.type == ToolEventType.SCAN,
            ToolEvent.undone_at.is_(None),
        )
        .group_by(ToolEvent.tool_id)
        .all()
    )
    out = {}
    for tool_id, created in rows:
        if not created:
            continue
        out[tool_id] = (
            created.astimezone(SHOP_TZ).date() if created.tzinfo else created.date()
        )
    return out


def _first_scan_date(tool_id: str):
    return _first_scan_dates_batch([tool_id]).get(tool_id)


def _unit_costs_batch(tools):
    """Latest received PO unit price per tool, else tools.unit_cost — one query."""
    from app.models.material_purchase import MaterialPurchase

    tool_ids = [t.id for t in tools if t.id]
    latest: dict[str, float] = {}
    if tool_ids:
        lines = (
            MaterialPurchase.query.filter(
                MaterialPurchase.tool_id.in_(tool_ids),
                MaterialPurchase.date_received.isnot(None),
                MaterialPurchase.cancelled_at.is_(None),
            )
            .order_by(
                MaterialPurchase.tool_id,
                MaterialPurchase.date_received.desc(),
            )
            .all()
        )
        for line in lines:
            if line.tool_id in latest:
                continue
            if line.unit_cost is not None:
                latest[line.tool_id] = float(line.unit_cost)
    out = {}
    for tool in tools:
        if tool.id in latest:
            out[tool.id] = latest[tool.id]
        elif tool.unit_cost is not None:
            out[tool.id] = float(tool.unit_cost)
        else:
            out[tool.id] = 0.0
    return out


def _unit_cost_for_abc(tool, lead_info_row):
    """Latest received PO unit price, else tools.unit_cost."""
    return _unit_costs_batch([tool]).get(tool.id, 0.0)


def _days_until_rop(on_hand, adu, rop, today, schedule):
    """Working days until on-hand reaches ROP at ADU per day (0 if already there)."""
    if adu is None or adu <= 0 or rop is None:
        return None
    if on_hand <= rop:
        return 0.0
    need = on_hand - rop
    days = need / adu
    nth = max(1, math.ceil(days))
    dates = shop_working_dates_after(today, nth, schedule)
    return float(nth) if dates else None


def _order_by_date(on_hand, adu, rop, today, schedule):
    dtr = _days_until_rop(on_hand, adu, rop, today, schedule)
    if dtr is None:
        return None
    if dtr <= 0:
        return today
    dates = shop_working_dates_after(today, max(1, math.ceil(dtr)), schedule)
    return dates[-1] if dates else None


def _assign_abc(items):
    """A = top ~70% of usage value, B = next ~20%, C = rest."""
    ranked = sorted(
        items,
        key=lambda r: float(r.get("usageValue90d") or 0),
        reverse=True,
    )
    total = sum(float(r.get("usageValue90d") or 0) for r in ranked)
    if total <= 0:
        for r in items:
            r["abcClass"] = None
        return
    cum = 0.0
    for r in ranked:
        cum += float(r.get("usageValue90d") or 0)
        share = cum / total
        if share <= 0.70:
            r["abcClass"] = "A"
        elif share <= 0.90:
            r["abcClass"] = "B"
        else:
            r["abcClass"] = "C"


def stock_snapshot_for_tools(tools, today: date | None = None) -> dict[str, dict]:
    """Same status / label / ROP as ``consumable_run_out``, batched for ``tools``.

    One scan-history query and one PO lead-time pass for the whole set (no N+1).
    Returns ``{tool_id: {status, label, reorderPoint, ...}}``.
    """
    today = today or shop_now().date()
    tools = [t for t in tools if t is not None]
    if not tools:
        return {}

    schedule = default_shop_schedule_by_dow()
    lead_info = _restock_lead_times(schedule)
    soon_days = _reorder_soon_days()
    tool_ids = [t.id for t in tools if t.id]
    first_scans = _first_scan_dates_batch(tool_ids)
    unit_costs = _unit_costs_batch(tools)

    # Widest window any tool might need: ADU lookback + ABC 90d lookback.
    earliest = min(first_scans.values()) if first_scans else today
    span_start = min(earliest, today - timedelta(days=ABC_LOOKBACK_DAYS + 5))
    # Pad for walking back working days when building the ADU window.
    scan_start = span_start - timedelta(days=ADU_WINDOW_MAX * 3 + 21)
    scans_by_tool = _scan_usage_by_day_batch(tool_ids, scan_start, today, schedule)

    # Precompute working-day calendars used by every tool (shared exceptions).
    adu_span = ADU_WINDOW_MAX * 3 + 14
    exceptions = load_calendar_exceptions(today - timedelta(days=adu_span), today)
    working_memo = shop_working_day_memo()
    if working_memo is None:
        working_memo = {}

    out: dict[str, dict] = {}
    for tool in tools:
        on_hand = float(Decimal(str(tool.quantity_on_hand or 0)))
        info = lead_info.get(tool.id) or {
            "measured_wd": [],
            "stated": None,
            "qty_received": 0.0,
            "first_order": None,
            "last_receive": None,
        }
        avg_lead, max_lead, lead_source = lead_times_working(info, today, schedule)

        first_scan = first_scans.get(tool.id)
        scan_days = 0
        daily_totals = []
        adu = None
        max_du = 0.0
        label = LABEL_NO_DATA
        tool_scans = scans_by_tool.get(tool.id) or {}

        if first_scan:
            since_start = shop_working_days_between(first_scan, today, schedule)
            n = min(ADU_WINDOW_MAX, max(since_start, 0))
            if n > 0:
                working_back = []
                d = today
                while len(working_back) < n and (today - d).days < adu_span:
                    if is_shop_working_day(d, schedule, exceptions, working_memo):
                        working_back.append(d)
                    d -= timedelta(days=1)
                working_back.reverse()
                scan_days = len(working_back)
                if working_back:
                    daily_totals = [tool_scans.get(day, 0.0) for day in working_back]
                    total_scans = sum(daily_totals)
                    adu = total_scans / scan_days if scan_days else None
                    max_du = max(daily_totals) if daily_totals else 0.0

            if scan_days >= ADU_COMPUTED_MIN_DAYS and adu is not None:
                label = LABEL_COMPUTED
            elif info.get("qty_received"):
                label = LABEL_ESTIMATED
            elif scan_days > 0 and adu is not None:
                label = LABEL_ESTIMATED
            else:
                label = LABEL_NO_DATA
        elif info.get("qty_received") and info.get("first_order") and info.get("last_receive"):
            label = LABEL_ESTIMATED
        else:
            label = LABEL_NO_DATA

        if label == LABEL_ESTIMATED and (adu is None or scan_days < ADU_COMPUTED_MIN_DAYS):
            covered = 0
            if info.get("first_order") and info.get("last_receive"):
                covered = shop_working_days_between(
                    info["first_order"], info["last_receive"], schedule
                )
            if covered > 0 and info.get("qty_received"):
                adu = float(info["qty_received"]) / covered
                max_du = max(max_du, adu)

        safety = safety_stock_scan(adu, max_du, avg_lead, max_lead) if adu is not None else None
        rop = None
        if adu is not None:
            rop = math.ceil(reorder_point(adu, avg_lead, safety or 0))
        suggested = 0
        if adu is not None:
            suggested = max(
                0,
                math.ceil(adu * REVIEW_PERIOD_DAYS + (safety or 0) - on_hand),
            )

        days_left, out_on = (None, None)
        if adu and adu > 0:
            days_left, out_on = run_out_date(on_hand, adu, today, schedule)

        days_to_rop = _days_until_rop(on_hand, adu, rop, today, schedule)
        order_by = _order_by_date(on_hand, adu, rop, today, schedule)
        status = (
            STOCK_NO_DATA
            if label == LABEL_NO_DATA or adu is None
            else stock_status(on_hand, adu, rop, days_to_rop, soon_days)
        )

        abc_start = today - timedelta(days=ABC_LOOKBACK_DAYS)
        scans_90 = sum(
            qty for day, qty in tool_scans.items() if abc_start <= day <= today
        )
        unit_cost = unit_costs.get(tool.id, 0.0)

        out[tool.id] = {
            "toolId": tool.id,
            "name": tool.name,
            "code": tool.code,
            "sizeSpec": tool.size_spec,
            "unit": tool.unit,
            "legacyUnit": tool.legacy_unit,
            "unitNeedsReview": bool(tool.unit_needs_review),
            "quantityOnHand": _round(on_hand, 2),
            "minimumStock": _round(tool.minimum_stock, 2),
            "adu": _round(adu, 4),
            "maxDailyUsage": _round(max_du, 4) if adu is not None else None,
            "scanWorkingDays": scan_days,
            "label": label,
            "leadTimeWorkingDaysAvg": _round(avg_lead, 1),
            "leadTimeWorkingDaysMax": _round(max_lead, 1),
            "leadTimeSource": lead_source,
            "safetyStock": _round(safety, 2) if safety is not None else None,
            "reorderPoint": _round(rop, 2) if rop is not None else None,
            "reorderLevel": _round(rop, 2) if rop is not None else None,
            "daysLeft": _round(days_left, 1),
            "runOutDate": out_on.isoformat() if out_on else None,
            "orderByDate": order_by.isoformat() if order_by else None,
            "daysToReorderLevel": _round(days_to_rop, 1),
            "suggestedOrderQty": suggested,
            "status": status,
            "usageValue90d": _round(scans_90 * unit_cost, 2),
            "unitCost": _round(unit_cost, 4) if unit_cost else None,
            "abcClass": None,
            "dailyUsage": _round(adu, 4),
            "estimatedOnHand": _round(on_hand, 2),
            "likelyOut": on_hand <= 0 and (adu or 0) > 0,
            "enoughData": label == LABEL_COMPUTED,
            "lowStock": status in (STOCK_REORDER, STOCK_REORDER_SOON),
        }
    return out


def list_stock_fields(tool) -> dict:
    """Compact forecast fields attached to GET /tools list rows."""
    snap = stock_snapshot_for_tools([tool]).get(tool.id) or {}
    return {
        "status": snap.get("status"),
        "label": snap.get("label"),
        "reorderPoint": snap.get("reorderPoint"),
        "reorderLevel": snap.get("reorderLevel"),
        "lowStock": snap.get("lowStock", False),
    }


def consumable_run_out(today: date | None = None):
    """Scan-based ADU, ROP, run-out, and ABC for every consumable."""
    today = today or shop_now().date()
    soon_days = _reorder_soon_days()
    tools = (
        Tool.query.filter_by(category=ToolCategory.CONSUMABLE).order_by(Tool.name).all()
    )
    snaps = stock_snapshot_for_tools(tools, today=today)
    items = [snaps[t.id] for t in tools if t.id in snaps]

    _assign_abc(items)

    status_order = {
        STOCK_REORDER: 0,
        STOCK_REORDER_SOON: 1,
        STOCK_OK: 2,
        STOCK_NO_DATA: 3,
    }
    items.sort(
        key=lambda r: (
            status_order.get(r["status"], 9),
            r["runOutDate"] is None,
            r["runOutDate"] or "",
            r["name"] or "",
        )
    )
    return {
        "label": "consumableRunOut",
        "method": "Scan-based ADU with max-based safety stock",
        "description": (
            "ADU = scans in the last N working days ÷ N (N = min(30, days since first scan)). "
            "Lead times are calendar (received − ordered) converted to shop working days. "
            "Safety stock = max(0, MaxDU × MaxLead − ADU × AvgLead). "
            "Reorder level = ceil(ADU × AvgLead + safety). "
            "Reorder now when on hand ≤ reorder level; reorder soon when that happens within "
            f"{soon_days} working days. "
            "Fewer than 14 scan days with PO history → Estimated ADU from receipts."
        ),
        "formulas": {
            "adu": "scans in last N working days / N; N = min(30, working days since first scan)",
            "safetyStock": "max(0, MaxDU × MaxLead − ADU × AvgLead)",
            "reorderLevel": "ceil(ADU × AvgLead + safetyStock)",
            "suggestedOrderQty": "ceil(ADU × 30 + safetyStock − onHand)",
            "leadTime": "working days between ordered and received (else supplier stated, else 7 calendar → working)",
        },
        "defaultLeadTimeDays": DEFAULT_LEAD_TIME_DAYS,
        "reorderSoonWorkingDays": soon_days,
        "reviewPeriodDays": REVIEW_PERIOD_DAYS,
        "today": today.isoformat(),
        "items": items,
    }
