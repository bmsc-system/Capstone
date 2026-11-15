"""Consumable replenishment forecast (objective 4.3).

The Office Staff keep the consumables and hand them to workers, so the system
knows how much is on hand and how fast each item is used. Hand-outs of an item
are irregular and come in uneven amounts, which is intermittent demand. The
forecast uses the Syntetos-Boylan Approximation of Croston's method: the size
of a hand-out and the days between hand-outs are each smoothed with the same
constant, which gives the usual use per day. From that:

    reorder point = use per day x (delivery lead time + safety days)
    runs out on   = today + stock on hand / use per day
    reorder on    = the day stock on hand reaches the reorder point

An item needs ``MIN_PURCHASES`` days with hand-outs (two intervals) for this.
Until then the same method is applied to its purchase history instead (when it
was bought and how much), which says when it is usually bought next; with fewer
than ``MIN_PURCHASES`` purchases it shows only when it was last bought.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

from app.models.tool import Tool, ToolCategory
from app.services import consumable_purchase_service as purchases
from app.services.schedule_calendar import shop_now

ALPHA = 0.2
MIN_PURCHASES = 3
DUE_SOON_DAYS = 7
SPEND_MONTHS = 6
DEFAULT_LEAD_DAYS = 7
SAFETY_MIN_DAYS = 3
COVER_DAYS = 30
BASIS_USAGE = "USAGE"
BASIS_PURCHASES = "PURCHASES"


def _round(value, places=1):
    return round(float(value), places) if value is not None else None


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _months_back(today: date, count: int) -> list[str]:
    keys, cursor = [], today.replace(day=1)
    for _ in range(count):
        keys.append(_month_key(cursor))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    return list(reversed(keys))


def croston_sba(events: list[tuple[date, float]], alpha: float = ALPHA) -> dict | None:
    """Smoothed purchase size and interval for ``events`` (date, quantity), oldest first.

    One purchase per day (same-day purchases are added together). Returns None
    with fewer than ``MIN_PURCHASES`` purchases. ``typicalErrorDays`` is the
    mean absolute error of the one-step-ahead interval forecasts, once there
    are at least two forecasts to check.
    """
    if len(events) < MIN_PURCHASES:
        return None
    intervals = [(b[0] - a[0]).days for a, b in zip(events, events[1:])]
    size = float(events[0][1])
    interval = float(intervals[0])
    errors = []
    for _day, qty in events[1:]:
        size += alpha * (float(qty) - size)
    for gap in intervals[1:]:
        errors.append(abs(gap - interval))
        interval += alpha * (gap - interval)
    interval = max(interval, 1.0)
    rate = (1 - alpha / 2) * size / interval
    return {
        "usualQuantity": size,
        "everyDays": interval,
        "dailyRate": rate,
        "typicalErrorDays": (sum(errors) / len(errors)) if len(errors) >= 2 else None,
    }


def _status(count, days_until):
    if count == 0:
        return "NO_HISTORY"
    if count < MIN_PURCHASES:
        return "LEARNING"
    if days_until < 0:
        return "OVERDUE"
    if days_until <= DUE_SOON_DAYS:
        return "DUE_SOON"
    return "OK"


def usage_events(tool_ids) -> dict:
    """{tool id: [(shop date, quantity handed out that day)]}, oldest first.
    Hand-outs that were undone do not count."""
    from app.models.tool_event import ToolEvent, ToolEventType
    from app.services.schedule_calendar import utc_to_shop

    ids = list(tool_ids)
    if not ids:
        return {}
    per_day = defaultdict(lambda: defaultdict(float))
    rows = ToolEvent.query.filter(
        ToolEvent.tool_id.in_(ids),
        ToolEvent.type.in_((ToolEventType.ISSUE, ToolEventType.SCAN)),
        ToolEvent.undone_at.is_(None),
    ).all()
    for e in rows:
        if e.created_at is None:
            continue
        per_day[e.tool_id][utc_to_shop(e.created_at).date()] += float(e.quantity or 0)
    return {tid: sorted(days.items()) for tid, days in per_day.items()}


def lead_days(tool_ids) -> dict:
    """{tool id: average days from ordering to delivery} from received supplier
    order lines; items never bought on an order use ``DEFAULT_LEAD_DAYS``."""
    from app.models.material_purchase import MaterialPurchase

    ids = list(tool_ids)
    if not ids:
        return {}
    spans = defaultdict(list)
    lines = MaterialPurchase.query.filter(
        MaterialPurchase.tool_id.in_(ids),
        MaterialPurchase.cancelled_at.is_(None),
        MaterialPurchase.date_received.isnot(None),
        MaterialPurchase.date_ordered.isnot(None),
    ).all()
    for ln in lines:
        spans[ln.tool_id].append(max((ln.date_received - ln.date_ordered).days, 0))
    return {tid: sum(v) / len(v) for tid, v in spans.items()}


def usage_forecast(events, on_hand: float, lead: float, today: date) -> dict | None:
    """Reorder point, run-out date and suggested quantity from hand-outs.
    None with fewer than ``MIN_PURCHASES`` days of hand-outs."""
    fit = croston_sba(events)
    if not fit or fit["dailyRate"] <= 0:
        return None
    daily = fit["dailyRate"]
    safety_days = max(SAFETY_MIN_DAYS, fit["typicalErrorDays"] or 0)
    reorder_point = daily * (lead + safety_days)
    days_until = math.floor((on_hand - reorder_point) / daily)
    cover = on_hand / daily
    target = daily * (COVER_DAYS + lead + safety_days)
    return {
        "dailyUse": daily,
        "usualIssue": fit["usualQuantity"],
        "issueEveryDays": fit["everyDays"],
        "typicalErrorDays": fit["typicalErrorDays"],
        "safetyDays": safety_days,
        "reorderPoint": reorder_point,
        "daysOfCover": cover,
        "runOutOn": today + timedelta(days=math.floor(cover)),
        "daysUntilReorder": days_until,
        "reorderOn": today + timedelta(days=days_until),
        "suggestedQuantity": max(math.ceil(target - on_hand), 1),
    }


def replenishment(today: date | None = None) -> dict:
    """One row per offered consumable, with the summary the dashboards use."""
    today = today or shop_now().date()
    tools = (
        Tool.query.filter(Tool.category == ToolCategory.CONSUMABLE, Tool.active.is_(True))
        .order_by(Tool.name)
        .all()
    )
    rows = purchases.purchase_rows([t.id for t in tools])
    by_tool = defaultdict(list)
    for row in rows:
        by_tool[row["toolId"]].append(row)

    used_by_tool = usage_events([t.id for t in tools])
    lead_by_tool = lead_days([t.id for t in tools])
    months = _months_back(today, SPEND_MONTHS)
    spend_by_month = {m: 0.0 for m in months}
    spend_30 = 0.0
    bought_30 = 0
    items = []
    for tool in tools:
        history = sorted(by_tool.get(tool.id, []), key=lambda r: r["purchasedOn"])
        per_day = defaultdict(float)
        for row in history:
            per_day[date.fromisoformat(row["purchasedOn"])] += row["quantity"]
            if (today - date.fromisoformat(row["purchasedOn"])).days < 30:
                bought_30 += 1
            if row["amount"] is not None:
                bought = date.fromisoformat(row["purchasedOn"])
                if _month_key(bought) in spend_by_month:
                    spend_by_month[_month_key(bought)] += row["amount"]
                if (today - bought).days < 30:
                    spend_30 += row["amount"]
        events = sorted(per_day.items())
        fit = croston_sba(events)
        last = history[-1] if history else None
        next_date = days_until = None
        on_hand = float(tool.quantity_on_hand or 0)
        lead = lead_by_tool.get(tool.id, DEFAULT_LEAD_DAYS)
        used = used_by_tool.get(tool.id, [])
        use = usage_forecast(used, on_hand, lead, today)
        if use:
            # Enough hand-outs: forecast from how fast the item is used.
            basis = BASIS_USAGE
            next_date, days_until = use["reorderOn"], use["daysUntilReorder"]
            status = _status(len(used), days_until)
        else:
            basis = BASIS_PURCHASES
            if fit:
                next_date = events[-1][0] + timedelta(days=round(fit["everyDays"]))
                days_until = (next_date - today).days
            status = _status(len(events), days_until if days_until is not None else 0)
        costs = [r["unitCost"] for r in history if r["unitCost"] is not None]
        items.append(
            {
                "toolId": tool.id,
                "name": tool.name,
                "shopTerm": tool.shop_term,
                "sizeSpec": tool.size_spec,
                "unit": tool.unit,
                "purchases": len(events),
                "lastBoughtOn": last["purchasedOn"] if last else None,
                "lastQuantity": last["quantity"] if last else None,
                "lastUnitCost": costs[-1] if costs else None,
                "everyDays": _round(fit["everyDays"]) if fit else None,
                # What to buy: enough to cover the next ``COVER_DAYS`` once hand-outs
                # are known; before that, the usual size of a purchase.
                "usualQuantity": (
                    use["suggestedQuantity"] if use
                    else _round(fit["usualQuantity"]) if fit else None
                ),
                "monthlyNeed": (
                    _round(use["dailyUse"] * 30) if use
                    else _round(fit["dailyRate"] * 30) if fit else None
                ),
                "typicalErrorDays": (
                    _round(use["typicalErrorDays"]) if use
                    else _round(fit["typicalErrorDays"]) if fit else None
                ),
                "basis": basis,
                "onHand": _round(on_hand, 2),
                "handOuts": len(used),
                "leadDays": _round(lead),
                "dailyUse": _round(use["dailyUse"], 2) if use else None,
                "safetyDays": _round(use["safetyDays"]) if use else None,
                "reorderPoint": _round(use["reorderPoint"]) if use else None,
                "daysOfCover": _round(use["daysOfCover"]) if use else None,
                "runOutOn": use["runOutOn"].isoformat() if use else None,
                "nextPurchaseOn": next_date.isoformat() if next_date else None,
                "daysUntilNext": days_until,
                "status": status,
            }
        )

    order = {"OVERDUE": 0, "DUE_SOON": 1, "OK": 2, "LEARNING": 3, "NO_HISTORY": 4}
    items.sort(key=lambda r: (order[r["status"]], r["daysUntilNext"] if r["daysUntilNext"] is not None else 10**6, r["name"].lower()))
    return {
        "asOf": today.isoformat(),
        "items": items,
        "summary": {
            "tracked": sum(1 for r in items if r["purchases"] or r["handOuts"]),
            "forecast": sum(1 for r in items if r["status"] in ("OK", "DUE_SOON", "OVERDUE")),
            "dueSoon": sum(1 for r in items if r["status"] == "DUE_SOON"),
            "overdue": sum(1 for r in items if r["status"] == "OVERDUE"),
            "spentLast30Days": round(spend_30, 2),
            "purchasesLast30Days": bought_30,
            "spendByMonth": [{"month": m, "amount": round(spend_by_month[m], 2)} for m in months],
        },
        "method": {
            "name": "Croston's method with the Syntetos-Boylan Approximation",
            "alpha": ALPHA,
            "minPurchases": MIN_PURCHASES,
            "dueSoonDays": DUE_SOON_DAYS,
            "safetyMinDays": SAFETY_MIN_DAYS,
            "coverDays": COVER_DAYS,
            "defaultLeadDays": DEFAULT_LEAD_DAYS,
        },
    }


def due_items(today: date | None = None) -> list[dict]:
    """Consumables due or overdue for buying, for the Office dashboard and supplier orders."""
    return [r for r in replenishment(today)["items"] if r["status"] in ("OVERDUE", "DUE_SOON")]
